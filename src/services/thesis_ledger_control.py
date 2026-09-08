# -*- coding: utf-8 -*-
"""ThesisLedger Control Contract V1 的 DSA 侧状态与校验。

该模块故意不复用主系统的 ``ProviderConfig``。DSA 只在自己的 SQLite 中保存
Provider 配置投影、Effective Policy、目录 generation 和控制诊断；主系统通过
HTTP Control Contract 传递 Desired Policy。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import queue
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CONTROL_CONTRACT_VERSION = 1
CONSUMER_NAMESPACE = "thesis-ledger"
CATALOG_JOB_LEASE_SECONDS = 300
CATALOG_JOB_LEASE_EXPIRED_CODE = "CATALOG_JOB_LEASE_EXPIRED"

CAPABILITIES = (
    "REALTIME_QUOTE",
    "DAILY_BAR",
    "FUND_NAV",
    "FUND_NAV_HISTORY",
    "FUND_HOLDINGS",
    "CHIP_SUMMARY",
)
INSTRUMENT_TYPES = (
    "STOCK",
    "ETF",
    "MUTUAL_FUND",
    "LOF",
    "INDEX",
    "BOND",
    "CONVERTIBLE_BOND",
)


def _manifest(
    provider_id: str,
    display_name: str,
    capabilities: dict[str, Iterable[str]],
    *,
    requires_credential: bool = False,
    upstream_sources: Iterable[tuple[str, str]] = (),
) -> dict[str, Any]:
    return {
        "providerId": provider_id,
        "displayName": display_name,
        "version": 1,
        "origin": "dsa",
        "upstreamSources": [
            {"sourceId": source_id, "displayName": source_name}
            for source_id, source_name in upstream_sources
        ],
        "capabilities": {key: sorted(set(value)) for key, value in capabilities.items()},
        "requiresCredential": requires_credential,
        "configSchema": {
            "credential": {"writeOnly": True, "required": requires_credential},
            "settings": {},
        },
    }


PROVIDER_MANIFESTS: dict[str, dict[str, Any]] = {
    "akshare": _manifest(
        "akshare",
        "AKShare",
        {
            "REALTIME_QUOTE": ("STOCK", "ETF"),
            "DAILY_BAR": ("STOCK", "ETF"),
            "FUND_NAV": ("MUTUAL_FUND",),
            "FUND_NAV_HISTORY": ("MUTUAL_FUND",),
            "FUND_HOLDINGS": ("MUTUAL_FUND",),
            "CHIP_SUMMARY": ("STOCK",),
        },
        upstream_sources=(
            ("eastmoney", "东方财富"),
            ("sina", "新浪财经"),
            ("tencent", "腾讯财经"),
        ),
    ),
    "efinance": _manifest(
        "efinance",
        "efinance",
        {
            "REALTIME_QUOTE": ("STOCK", "ETF"),
            "DAILY_BAR": ("STOCK", "ETF"),
            "FUND_NAV": ("MUTUAL_FUND",),
            "FUND_NAV_HISTORY": ("MUTUAL_FUND",),
        },
        upstream_sources=(("eastmoney", "东方财富"),),
    ),
    "tencent": _manifest(
        "tencent",
        "腾讯财经",
        {
            "DAILY_BAR": ("STOCK", "ETF"),
        },
        upstream_sources=(("tencent", "腾讯财经"),),
    ),
}


def _provider_configured(
    manifest: dict[str, Any],
    config: sqlite3.Row | None,
) -> bool:
    if not manifest.get("requiresCredential", False):
        return True
    return bool(config and config["credential_ciphertext"])

# These are only the initial product policy defaults. They are seeded by the
# ThesisLedger side as Desired revision 1; DSA never silently adds them to a
# user supplied policy.
DEFAULT_ROUTES: dict[str, dict[str, list[str]]] = {
    "REALTIME_QUOTE": {
        "STOCK": ["akshare", "efinance"],
        "ETF": ["akshare", "efinance"],
    },
    "DAILY_BAR": {
        "STOCK": ["akshare", "efinance"],
        "ETF": ["akshare", "efinance"],
    },
    "FUND_NAV": {"MUTUAL_FUND": ["akshare", "efinance"]},
    "FUND_NAV_HISTORY": {"MUTUAL_FUND": ["akshare", "efinance"]},
    "FUND_HOLDINGS": {"MUTUAL_FUND": ["akshare"]},
    "CHIP_SUMMARY": {"STOCK": ["akshare"]},
}

DEFAULT_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "canonicalCode": "000001",
        "instrumentType": "STOCK",
        "market": "SZ",
        "displayName": "平安银行",
    },
    {
        "canonicalCode": "600519",
        "instrumentType": "STOCK",
        "market": "SH",
        "displayName": "贵州茅台",
    },
    {
        "canonicalCode": "510300",
        "instrumentType": "ETF",
        "market": "SH",
        "displayName": "沪深300ETF",
    },
    {
        "canonicalCode": "000001",
        "instrumentType": "MUTUAL_FUND",
        "market": "OF",
        "displayName": "华夏成长混合",
    },
    {
        "canonicalCode": "110022",
        "instrumentType": "MUTUAL_FUND",
        "market": "OF",
        "displayName": "易方达消费行业股票",
    },
    {
        "canonicalCode": "000300",
        "instrumentType": "INDEX",
        "market": "SH",
        "displayName": "沪深300指数",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _lease_expires_at(started_at: str) -> str:
    parsed = _parse_utc(started_at) or datetime.now(timezone.utc)
    return (parsed + timedelta(seconds=CATALOG_JOB_LEASE_SECONDS)).isoformat()


def _lease_is_valid(lease_expires_at: Any, now: str) -> bool:
    expires = _parse_utc(lease_expires_at)
    current = _parse_utc(now)
    return expires is not None and current is not None and expires > current


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None, fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _request_id(value: Any) -> str:
    text = str(value or "").strip()
    return text[:128] if text else str(uuid.uuid4())


class ControlContractError(Exception):
    """A stable, user-safe error at the Control Contract boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 422,
        request_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id or str(uuid.uuid4())
        self.details = details or {}

    def detail(self) -> dict[str, Any]:
        return {
            "contractVersion": CONTROL_CONTRACT_VERSION,
            "code": self.code,
            "message": self.message,
            "requestId": self.request_id,
            "diagnosticId": self.request_id,
            **self.details,
        }


def _database_path() -> str:
    configured = os.getenv("DATABASE_PATH", "./data/stock_analysis.db").strip()
    return configured or "./data/stock_analysis.db"


def _secret_key() -> tuple[str, bytes]:
    raw = (
        os.getenv("THESIS_LEDGER_DSA_SECRET_KEY", "").strip()
        or os.getenv("DSA_SECRET_KEY", "").strip()
    )
    if not raw:
        raise ControlContractError(
            "SECRET_KEY_MISSING",
            "DSA Secret Key 未配置，不能保存 Provider 凭证",
            status_code=503,
        )
    version = os.getenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "v1").strip() or "v1"
    return version, hashlib.sha256(raw.encode("utf-8")).digest()


def _secret_key_candidates() -> dict[str, bytes]:
    """Return current and explicitly retained previous keys by version."""

    current_version, current_key = _secret_key()
    candidates = {current_version: current_key}
    previous_raw = os.getenv("THESIS_LEDGER_DSA_SECRET_KEY_PREVIOUS", "").strip()
    previous_version = (
        os.getenv("THESIS_LEDGER_DSA_SECRET_KEY_PREVIOUS_VERSION", "v1").strip() or "v1"
    )
    if previous_raw and previous_version not in candidates:
        candidates[previous_version] = hashlib.sha256(previous_raw.encode("utf-8")).digest()
    return candidates


def _encrypt_secret(value: str) -> tuple[str, str]:
    version, key = _secret_key()
    nonce = secrets.token_bytes(16)
    stream = bytearray()
    counter = 0
    while len(stream) < len(value.encode("utf-8")):
        stream.extend(hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest())
        counter += 1
    plaintext = value.encode("utf-8")
    ciphertext = bytes(left ^ right for left, right in zip(plaintext, stream))
    tag = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
    return version, base64.urlsafe_b64encode(nonce + ciphertext + tag).decode("ascii")


def _decrypt_secret(version: str, encoded: str) -> str:
    """Decrypt one stored credential using the explicitly configured key ring."""

    key = _secret_key_candidates().get(str(version or "").strip())
    if key is None:
        raise ControlContractError(
            "SECRET_KEY_UNAVAILABLE",
            "Provider 凭证所需的旧 DSA Secret Key 未配置",
            status_code=503,
        )
    try:
        payload = base64.urlsafe_b64decode(str(encoded).encode("ascii"))
        if len(payload) < 16 + 32:
            raise ValueError("ciphertext too short")
        nonce, ciphertext, tag = payload[:16], payload[16:-32], payload[-32:]
        expected = hmac.new(key, nonce + ciphertext, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("ciphertext authentication failed")
        return bytes(
            left ^ right
            for left, right in zip(
                ciphertext,
                b"".join(
                    hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
                    for counter in range((len(ciphertext) + 31) // 32)
                ),
            )
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError, TypeError, base64.binascii.Error) as exc:
        raise ControlContractError(
            "SECRET_CREDENTIAL_INVALID",
            "Provider 凭证密文校验失败",
            status_code=503,
        ) from exc


def _validate_provider_id(provider_id: Any, request_id: str) -> str:
    normalized = str(provider_id or "").strip().lower()
    if normalized not in PROVIDER_MANIFESTS:
        raise ControlContractError(
            "UNKNOWN_PROVIDER",
            f"Provider {provider_id!s} 不存在",
            request_id=request_id,
        )
    return normalized


def _normalize_routes(routes: Any, request_id: str) -> dict[str, dict[str, list[str]]]:
    if not isinstance(routes, dict):
        raise ControlContractError(
            "INVALID_POLICY_SCHEMA",
            "routes 必须是 Capability 到 InstrumentType 的对象",
            request_id=request_id,
        )
    normalized: dict[str, dict[str, list[str]]] = {}
    for raw_capability, raw_types in routes.items():
        capability = str(raw_capability).strip().upper()
        if capability not in CAPABILITIES:
            raise ControlContractError(
                "UNSUPPORTED_CAPABILITY",
                f"Capability {raw_capability!s} 不支持",
                request_id=request_id,
            )
        if not isinstance(raw_types, dict):
            raise ControlContractError(
                "INVALID_POLICY_SCHEMA",
                f"routes.{capability} 必须是 InstrumentType 对象",
                request_id=request_id,
            )
        normalized_types: dict[str, list[str]] = {}
        for raw_type, raw_providers in raw_types.items():
            instrument_type = str(raw_type).strip().upper()
            if instrument_type not in INSTRUMENT_TYPES:
                raise ControlContractError(
                    "UNSUPPORTED_INSTRUMENT_TYPE",
                    f"InstrumentType {raw_type!s} 不支持",
                    request_id=request_id,
                )
            if not any(
                instrument_type in manifest.get("capabilities", {}).get(capability, [])
                for manifest in PROVIDER_MANIFESTS.values()
            ):
                raise ControlContractError(
                    "UNSUPPORTED_ROUTE",
                    f"没有 Provider 支持 {capability}/{instrument_type}",
                    request_id=request_id,
                )
            if not isinstance(raw_providers, list):
                raise ControlContractError(
                    "INVALID_POLICY_SCHEMA",
                    f"routes.{capability}.{instrument_type} 必须是 Provider ID 数组",
                    request_id=request_id,
                )
            provider_ids: list[str] = []
            for raw_provider in raw_providers:
                provider_id = _validate_provider_id(raw_provider, request_id)
                if provider_id in provider_ids:
                    raise ControlContractError(
                        "DUPLICATE_PROVIDER",
                        f"route {capability}/{instrument_type} 中 Provider 重复",
                        request_id=request_id,
                    )
                if instrument_type not in PROVIDER_MANIFESTS[provider_id]["capabilities"].get(
                    capability, []
                ):
                    raise ControlContractError(
                        "UNSUPPORTED_ROUTE",
                        f"Provider {provider_id} 不支持 {capability}/{instrument_type}",
                        request_id=request_id,
                    )
                provider_ids.append(provider_id)
            normalized_types[instrument_type] = provider_ids
        normalized[capability] = normalized_types
    return normalized


def normalize_policy(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = _request_id(payload.get("requestId"))
    if payload.get("contractVersion") != CONTROL_CONTRACT_VERSION:
        raise ControlContractError(
            "CONTROL_CONTRACT_UNSUPPORTED",
            "Control Contract 版本不兼容",
            request_id=request_id,
        )
    if payload.get("consumer") != CONSUMER_NAMESPACE:
        raise ControlContractError(
            "INVALID_CONSUMER",
            "Control Contract consumer namespace 不正确",
            request_id=request_id,
        )
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise ControlContractError(
            "INVALID_REVISION",
            "Policy revision 必须是正整数",
            request_id=request_id,
        )
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        raise ControlContractError(
            "INVALID_POLICY_SCHEMA",
            "Policy enabled 必须是布尔值",
            request_id=request_id,
        )
    return {
        "contractVersion": CONTROL_CONTRACT_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "revision": revision,
        "enabled": enabled,
        "routes": _normalize_routes(payload.get("routes", {}), request_id),
        "requestId": request_id,
    }


class ThesisLedgerControlStore:
    """Small SQLite repository for the DSA-owned control projection."""

    _schema_lock = threading.RLock()

    def __init__(self, database_path: str | None = None) -> None:
        self.database_path = database_path or _database_path()
        # Every store instance gets a fencing identity. A new service process
        # therefore cannot accidentally finalize a lease owned by its
        # predecessor, while valid running jobs remain globally deduplicated.
        self._catalog_job_owner = f"pid:{os.getpid()}:{uuid.uuid4().hex}"
        self._ensure_schema()
        self._rotate_provider_credentials()

    def _connect(self) -> sqlite3.Connection:
        if self.database_path != ":memory:":
            Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        if self.database_path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self) -> None:
        with self._schema_lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS thesis_ledger_policy_state (
                    consumer TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    routes_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    effective_json TEXT NOT NULL,
                    last_error_json TEXT,
                    request_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thesis_ledger_policy_history (
                    consumer TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    enabled INTEGER NOT NULL,
                    routes_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    effective_json TEXT NOT NULL,
                    last_error_json TEXT,
                    request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (consumer, revision)
                );
                CREATE TABLE IF NOT EXISTS thesis_ledger_provider_config (
                    provider_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    credential_ciphertext TEXT,
                    secret_key_version TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thesis_ledger_provider_tombstone (
                    provider_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    reason TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    removed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thesis_ledger_provider_health (
                    scope_key TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    instrument_type TEXT NOT NULL,
                    state TEXT NOT NULL,
                    circuit TEXT NOT NULL DEFAULT 'closed',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER,
                    error_code TEXT,
                    checked_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thesis_ledger_catalog_generation (
                    generation INTEGER PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    cursor TEXT NOT NULL,
                    complete INTEGER NOT NULL,
                    items_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thesis_ledger_catalog_job (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    error_json TEXT,
                    owner TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS thesis_ledger_catalog_ack (
                    consumer TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL,
                    checksum TEXT NOT NULL,
                    cursor TEXT NOT NULL,
                    acknowledged_at TEXT NOT NULL
                );
                """
            )
            # Existing DSA volumes predate Catalog Job leases. Keep the
            # upgrade additive and idempotent so restarting against an old
            # SQLite file is sufficient; deleting the volume is never needed.
            job_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(thesis_ledger_catalog_job)"
                ).fetchall()
            }
            if "owner" not in job_columns:
                connection.execute(
                    "ALTER TABLE thesis_ledger_catalog_job ADD COLUMN owner TEXT"
                )
            if "lease_expires_at" not in job_columns:
                connection.execute(
                    "ALTER TABLE thesis_ledger_catalog_job ADD COLUMN lease_expires_at TEXT"
                )
            if "updated_at" not in job_columns:
                connection.execute(
                    "ALTER TABLE thesis_ledger_catalog_job ADD COLUMN updated_at TEXT"
                )
                connection.execute(
                    """
                    UPDATE thesis_ledger_catalog_job
                    SET updated_at = COALESCE(created_at, ?)
                    WHERE updated_at IS NULL
                    """,
                    (_utc_now(),),
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS thesis_ledger_catalog_job_status_lease_idx
                ON thesis_ledger_catalog_job (status, lease_expires_at, created_at)
                """
            )

    def _configuration(self, connection: sqlite3.Connection) -> dict[str, sqlite3.Row]:
        return {
            row["provider_id"]: row
            for row in connection.execute(
                "SELECT * FROM thesis_ledger_provider_config"
            ).fetchall()
        }

    def _rotate_provider_credentials(self) -> None:
        """Re-encrypt credentials with the current key when a previous key is retained."""

        try:
            current_version, _ = _secret_key()
        except ControlContractError:
            # A store without configured credentials must remain usable for read-only
            # Contract and fixture tests; saving credentials still fails closed.
            return
        with self._schema_lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT provider_id, credential_ciphertext, secret_key_version
                FROM thesis_ledger_provider_config
                WHERE credential_ciphertext IS NOT NULL
                  AND secret_key_version IS NOT NULL
                  AND secret_key_version != ?
                """,
                (current_version,),
            ).fetchall()
            updates: list[tuple[str, str]] = []
            for row in rows:
                try:
                    plaintext = _decrypt_secret(
                        str(row["secret_key_version"]),
                        str(row["credential_ciphertext"]),
                    )
                    _, ciphertext = _encrypt_secret(plaintext)
                except ControlContractError:
                    logger.warning(
                        "Provider credential rotation deferred provider=%s version=%s",
                        row["provider_id"],
                        row["secret_key_version"],
                    )
                    # Do not partially rotate the key ring. The previous key
                    # must remain available until every stored credential is
                    # verified under the new key.
                    return
                updates.append((ciphertext, str(row["provider_id"])))
            if not updates:
                return
            now = _utc_now()
            connection.execute("BEGIN IMMEDIATE")
            try:
                for ciphertext, provider_id in updates:
                    connection.execute(
                        """
                        UPDATE thesis_ledger_provider_config
                        SET credential_ciphertext=?, secret_key_version=?, updated_at=?
                        WHERE provider_id=?
                        """,
                        (ciphertext, current_version, now, provider_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def _health(
        self,
        connection: sqlite3.Connection,
        provider_id: str,
        capability: str,
        instrument_type: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM thesis_ledger_provider_health
            WHERE scope_key = ?
            """,
            (f"{CONSUMER_NAMESPACE}:{provider_id}:{capability}:{instrument_type}",),
        ).fetchone()

    def _effective(
        self,
        connection: sqlite3.Connection,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        configurations = self._configuration(connection)
        route_status: dict[str, dict[str, Any]] = {}
        for capability, type_routes in policy["routes"].items():
            route_status[capability] = {}
            for instrument_type, provider_ids in type_routes.items():
                entries: list[dict[str, Any]] = []
                eligible: list[str] = []
                for provider_id in provider_ids:
                    config = configurations.get(provider_id)
                    health = self._health(connection, provider_id, capability, instrument_type)
                    configured = _provider_configured(PROVIDER_MANIFESTS[provider_id], config)
                    enabled = config is None or bool(config["enabled"])
                    state = str(health["state"] if health else "unknown")
                    circuit = str(health["circuit"] if health else "closed")
                    available = configured and enabled and circuit != "open"
                    reason = None
                    if not configured:
                        reason = "provider_not_configured"
                    elif not enabled:
                        reason = "provider_disabled"
                    elif circuit == "open":
                        reason = "circuit_open"
                    else:
                        eligible.append(provider_id)
                    entries.append(
                        {
                            "providerId": provider_id,
                            "configured": configured,
                            "enabled": enabled,
                            "available": available,
                            "eligible": available,
                            "health": state,
                            "circuit": circuit,
                            "reason": reason,
                        }
                    )
                route_status[capability][instrument_type] = {
                    "providers": entries,
                    "eligibleProviderIds": eligible,
                    "reason": None if eligible else "NO_ELIGIBLE_PROVIDER",
                }
        return {
            "contractVersion": CONTROL_CONTRACT_VERSION,
            "consumer": CONSUMER_NAMESPACE,
            "revision": policy["revision"],
            "sourceDesiredRevision": policy["revision"],
            "enabled": policy["enabled"],
            "routes": policy["routes"],
            "routeStatus": route_status,
            "appliedAt": _utc_now(),
        }

    def _current_state(self, connection: sqlite3.Connection) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM thesis_ledger_policy_state WHERE consumer = ?",
            (CONSUMER_NAMESPACE,),
        ).fetchone()

    def apply_policy(self, payload: dict[str, Any]) -> dict[str, Any]:
        policy = normalize_policy(payload)
        request_id = policy["requestId"]
        with self._schema_lock, self._connect() as connection:
            try:
                # Read, validate and write under the same SQLite writer lock. A
                # process-local lock cannot protect separate workers or
                # connections, while BEGIN IMMEDIATE makes the revision check
                # observe the latest committed policy before any write occurs.
                connection.execute("BEGIN IMMEDIATE")
                current = self._current_state(connection)
                if current is not None:
                    current_revision = int(current["revision"])
                    if policy["revision"] < current_revision:
                        raise ControlContractError(
                            "STALE_REVISION",
                            f"Policy revision {policy['revision']} 早于当前 revision {current_revision}",
                            request_id=request_id,
                        )
                    if policy["revision"] == current_revision:
                        same = (
                            bool(current["enabled"]) == policy["enabled"]
                            and _json_load(current["routes_json"], {}) == policy["routes"]
                        )
                        if not same:
                            raise ControlContractError(
                                "REVISION_CONFLICT",
                                "相同 revision 的 Policy 内容不同",
                                request_id=request_id,
                            )
                        connection.commit()
                        return {
                            "status": current["status"],
                            "idempotent": True,
                            "desired": {
                                "contractVersion": CONTROL_CONTRACT_VERSION,
                                "consumer": CONSUMER_NAMESPACE,
                                "revision": current_revision,
                                "enabled": bool(current["enabled"]),
                                "routes": _json_load(current["routes_json"], {}),
                            },
                            "effective": _json_load(current["effective_json"], {}),
                            "requestId": request_id,
                        }

                effective = self._effective(connection, policy)
                now = _utc_now()
                desired_json = _json(policy["routes"])
                effective_json = _json(effective)
                connection.execute(
                    """
                    INSERT INTO thesis_ledger_policy_state
                    (consumer, revision, enabled, routes_json, status, effective_json,
                     last_error_json, request_id, updated_at)
                    VALUES (?, ?, ?, ?, 'applied', ?, NULL, ?, ?)
                    ON CONFLICT(consumer) DO UPDATE SET
                      revision=excluded.revision,
                      enabled=excluded.enabled,
                      routes_json=excluded.routes_json,
                      status=excluded.status,
                      effective_json=excluded.effective_json,
                      last_error_json=NULL,
                      request_id=excluded.request_id,
                      updated_at=excluded.updated_at
                    """,
                    (
                        CONSUMER_NAMESPACE,
                        policy["revision"],
                        int(policy["enabled"]),
                        desired_json,
                        effective_json,
                        request_id,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO thesis_ledger_policy_history
                    (consumer, revision, enabled, routes_json, status, effective_json,
                     last_error_json, request_id, created_at)
                    VALUES (?, ?, ?, ?, 'applied', ?, NULL, ?, ?)
                    """,
                    (
                        CONSUMER_NAMESPACE,
                        policy["revision"],
                        int(policy["enabled"]),
                        desired_json,
                        effective_json,
                        request_id,
                        now,
                    ),
                )
                connection.commit()
            except ControlContractError:
                connection.rollback()
                raise
            except sqlite3.IntegrityError as error:
                connection.rollback()
                raise ControlContractError(
                    "REVISION_CONFLICT",
                    "Policy revision 竞争冲突，请重试",
                    request_id=request_id,
                ) from error
            except sqlite3.OperationalError as error:
                connection.rollback()
                if "locked" in str(error).lower():
                    raise ControlContractError(
                        "POLICY_APPLY_CONFLICT",
                        "Policy Apply 正在竞争，请重试",
                        status_code=409,
                        request_id=request_id,
                    ) from error
                raise
            except Exception:
                connection.rollback()
                raise
            return {
                "status": "applied",
                "idempotent": False,
                "desired": {
                    "contractVersion": CONTROL_CONTRACT_VERSION,
                    "consumer": CONSUMER_NAMESPACE,
                    "revision": policy["revision"],
                    "enabled": policy["enabled"],
                    "routes": policy["routes"],
                },
                "effective": effective,
                "requestId": request_id,
            }

    def policy_projection(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            current = self._current_state(connection)
            if current is None:
                return None
            return {
                "status": current["status"],
                "desired": {
                    "contractVersion": CONTROL_CONTRACT_VERSION,
                    "consumer": CONSUMER_NAMESPACE,
                    "revision": int(current["revision"]),
                    "enabled": bool(current["enabled"]),
                    "routes": _json_load(current["routes_json"], {}),
                },
                "effective": _json_load(current["effective_json"], {}),
                "lastError": _json_load(current["last_error_json"], None),
                "updatedAt": current["updated_at"],
            }

    def effective_policy(self) -> dict[str, Any] | None:
        projection = self.policy_projection()
        return projection["effective"] if projection else None

    def route(
        self,
        capability: str,
        instrument_type: str,
        *,
        include_circuit_open: bool = False,
    ) -> list[str]:
        effective = self.effective_policy()
        if not effective or not effective.get("enabled"):
            return []
        status = (
            effective.get("routeStatus", {})
            .get(capability.upper(), {})
            .get(instrument_type.upper(), {})
        )
        if not include_circuit_open:
            return [str(value) for value in status.get("eligibleProviderIds", [])]
        return [
            str(entry["providerId"])
            for entry in status.get("providers", [])
            if entry.get("configured") and entry.get("enabled")
        ]

    def health(
        self,
        provider_id: str,
        capability: str,
        instrument_type: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = self._health(connection, provider_id, capability, instrument_type)
            return dict(row) if row else None

    def provider_registry(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            configurations = self._configuration(connection)
            health_rows = connection.execute(
                """
                SELECT provider_id, capability, instrument_type, state, circuit,
                       consecutive_failures, latency_ms, error_code, checked_at
                FROM thesis_ledger_provider_health
                ORDER BY provider_id, capability, instrument_type
                """
            ).fetchall()
            health_by_provider: dict[str, list[dict[str, Any]]] = {}
            for row in health_rows:
                health_by_provider.setdefault(row["provider_id"], []).append(
                    {
                        "capability": row["capability"],
                        "instrumentType": row["instrument_type"],
                        "state": row["state"],
                        "circuit": row["circuit"],
                        "consecutiveFailures": int(row["consecutive_failures"]),
                        "latencyMs": row["latency_ms"],
                        "errorCode": row["error_code"],
                        "checkedAt": row["checked_at"],
                    }
                )
            result = []
            for provider_id, manifest in PROVIDER_MANIFESTS.items():
                config = configurations.get(provider_id)
                tombstone = connection.execute(
                    "SELECT * FROM thesis_ledger_provider_tombstone WHERE provider_id = ?",
                    (provider_id,),
                ).fetchone()
                result.append(
                    {
                        **manifest,
                        "configured": False
                        if tombstone
                        else _provider_configured(manifest, config),
                        "enabled": bool(config["enabled"]) if config else True,
                        "credentialConfigured": bool(
                            config and config["credential_ciphertext"]
                        ),
                        "updatedAt": config["updated_at"] if config else None,
                        "health": {"scopes": health_by_provider.get(provider_id, [])},
                        "tombstone":
                            {
                                "providerId": provider_id,
                                "displayName": tombstone["display_name"],
                                "reason": tombstone["reason"],
                                "removedAt": tombstone["removed_at"],
                            }
                            if tombstone
                            else None,
                    }
                )
            return result

    def remove_provider(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = _request_id(payload.get("requestId"))
        provider_id = _validate_provider_id(provider_id, request_id)
        manifest = PROVIDER_MANIFESTS[provider_id]
        reason = str(payload.get("reason") or "removed_by_consumer")[:200]
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                "Provider tombstone metadata 必须是对象",
                request_id=request_id,
            )
        now = _utc_now()
        with self._schema_lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_config
                (provider_id, enabled, settings_json, credential_ciphertext,
                 secret_key_version, updated_at)
                VALUES (?, 0, '{}', NULL, NULL, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  enabled=0,
                  credential_ciphertext=NULL,
                  secret_key_version=NULL,
                  updated_at=excluded.updated_at
                """,
                (provider_id, now),
            )
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_tombstone
                (provider_id, display_name, reason, metadata_json, removed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  reason=excluded.reason,
                  metadata_json=excluded.metadata_json,
                  removed_at=excluded.removed_at
                """,
                (provider_id, manifest["displayName"], reason, _json(metadata), now),
            )
            current = self._current_state(connection)
            effective = _json_load(current["effective_json"], {}) if current else {}
            if current:
                policy = {
                    "revision": int(current["revision"]),
                    "enabled": bool(current["enabled"]),
                    "routes": _json_load(current["routes_json"], {}),
                }
                effective = self._effective(connection, policy)
                connection.execute(
                    "UPDATE thesis_ledger_policy_state SET effective_json=?, updated_at=? WHERE consumer=?",
                    (_json(effective), now, CONSUMER_NAMESPACE),
                )
            connection.commit()
        return {
            "contractVersion": CONTROL_CONTRACT_VERSION,
            "consumer": CONSUMER_NAMESPACE,
            "providerId": provider_id,
            "removed": True,
            "tombstone": {
                "providerId": provider_id,
                "displayName": manifest["displayName"],
                "reason": reason,
                "removedAt": now,
            },
            "effective": effective,
            "requestId": request_id,
        }

    def save_provider_config(self, provider_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = _request_id(payload.get("requestId"))
        provider_id = _validate_provider_id(provider_id, request_id)
        enabled = payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                "Provider enabled 必须是布尔值",
                request_id=request_id,
            )
        settings = payload.get("settings", {})
        if not isinstance(settings, dict):
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                "Provider settings 必须是对象",
                request_id=request_id,
            )
        credential = payload.get("credential")
        clear_credentials = payload.get("clearCredentials", False)
        if not isinstance(clear_credentials, bool):
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                "clearCredentials 必须是布尔值",
                request_id=request_id,
            )
        encrypted: str | None | object = object()
        key_version: str | None | object = object()
        if clear_credentials:
            encrypted, key_version = None, None
        elif credential is not None and str(credential).strip():
            key_version, encrypted = _encrypt_secret(str(credential))

        with self._schema_lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM thesis_ledger_provider_config WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            if encrypted.__class__ is object:
                encrypted = existing["credential_ciphertext"] if existing else None
                key_version = existing["secret_key_version"] if existing else None
            now = _utc_now()
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_config
                (provider_id, enabled, settings_json, credential_ciphertext,
                 secret_key_version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  enabled=excluded.enabled,
                  settings_json=excluded.settings_json,
                  credential_ciphertext=excluded.credential_ciphertext,
                  secret_key_version=excluded.secret_key_version,
                  updated_at=excluded.updated_at
                """,
                (
                    provider_id,
                    int(enabled),
                    _json(settings),
                    encrypted,
                    key_version,
                    now,
                ),
            )
            connection.execute(
                "DELETE FROM thesis_ledger_provider_tombstone WHERE provider_id = ?",
                (provider_id,),
            )
            current = self._current_state(connection)
            effective = _json_load(current["effective_json"], {}) if current else {}
            if current:
                policy = {
                    "revision": int(current["revision"]),
                    "enabled": bool(current["enabled"]),
                    "routes": _json_load(current["routes_json"], {}),
                }
                effective = self._effective(connection, policy)
                connection.execute(
                    "UPDATE thesis_ledger_policy_state SET effective_json=?, updated_at=? WHERE consumer=?",
                    (_json(effective), now, CONSUMER_NAMESPACE),
                )
            connection.commit()
            return {
                "providerId": provider_id,
                "enabled": enabled,
                "configured": _provider_configured(
                    PROVIDER_MANIFESTS[provider_id],
                    connection.execute(
                        "SELECT * FROM thesis_ledger_provider_config WHERE provider_id = ?",
                        (provider_id,),
                    ).fetchone(),
                ),
                "credentialConfigured": bool(encrypted),
                "settings": settings,
                "secretKeyVersion": key_version,
                "updatedAt": now,
                "effective": effective,
                "requestId": request_id,
            }

    def record_health(
        self,
        provider_id: str,
        capability: str,
        instrument_type: str,
        *,
        state: str,
        circuit: str = "closed",
        consecutive_failures: int = 0,
        latency_ms: int | None = None,
        error_code: str | None = None,
    ) -> None:
        scope_key = f"{CONSUMER_NAMESPACE}:{provider_id}:{capability}:{instrument_type}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_health
                (scope_key, provider_id, capability, instrument_type, state, circuit,
                 consecutive_failures, latency_ms, error_code, checked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                  state=excluded.state,
                  circuit=excluded.circuit,
                  consecutive_failures=excluded.consecutive_failures,
                  latency_ms=excluded.latency_ms,
                  error_code=excluded.error_code,
                  checked_at=excluded.checked_at
                """,
                (
                    scope_key,
                    provider_id,
                    capability,
                    instrument_type,
                    state,
                    circuit,
                    consecutive_failures,
                    latency_ms,
                    error_code,
                    _utc_now(),
                ),
            )
            current = self._current_state(connection)
            if current is not None:
                policy = {
                    "revision": int(current["revision"]),
                    "enabled": bool(current["enabled"]),
                    "routes": _json_load(current["routes_json"], {}),
                }
                effective = self._effective(connection, policy)
                connection.execute(
                    "UPDATE thesis_ledger_policy_state SET effective_json=?, updated_at=? WHERE consumer=?",
                    (_json(effective), _utc_now(), CONSUMER_NAMESPACE),
                )
            connection.commit()

    def _ensure_catalog(self, connection: sqlite3.Connection) -> sqlite3.Row:
        existing = connection.execute(
            "SELECT * FROM thesis_ledger_catalog_generation ORDER BY generation DESC LIMIT 1"
        ).fetchone()
        if existing:
            return existing
        if os.getenv("THESIS_LEDGER_FIXTURE_MODE", "false").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            raise ControlContractError(
                "CATALOG_NOT_READY",
                "Catalog 尚未生成，请先触发同步任务",
                status_code=409,
            )
        items = sorted(DEFAULT_CATALOG, key=lambda item: (item["canonicalCode"], item["market"], item["instrumentType"]))
        checksum = hashlib.sha256(_json(items).encode("utf-8")).hexdigest()
        cursor = "generation:1"
        connection.execute(
            """
            INSERT INTO thesis_ledger_catalog_generation
            (generation, checksum, cursor, complete, items_json, created_at)
            VALUES (1, ?, ?, 1, ?, ?)
            """,
            (checksum, cursor, _json(items), _utc_now()),
        )
        connection.commit()
        return connection.execute(
            "SELECT * FROM thesis_ledger_catalog_generation WHERE generation=1"
        ).fetchone()

    def catalog_snapshot(self, cursor: str | None = None) -> dict[str, Any]:
        with self._connect() as connection:
            row = self._ensure_catalog(connection)
            if cursor and cursor not in {"0", "generation:0", row["cursor"]}:
                raise ControlContractError(
                    "CATALOG_CURSOR_EXPIRED",
                    "Catalog cursor 已过期，需要完整 snapshot",
                    status_code=409,
                )
            return {
                "contractVersion": CONTROL_CONTRACT_VERSION,
                "generation": int(row["generation"]),
                "checksum": row["checksum"],
                "cursor": row["cursor"],
                "complete": bool(row["complete"]),
                "items": _json_load(row["items_json"], []),
            }

    def catalog_delta(self, cursor: str) -> dict[str, Any]:
        with self._connect() as connection:
            latest = self._ensure_catalog(connection)
            if cursor == latest["cursor"]:
                return {
                    "contractVersion": CONTROL_CONTRACT_VERSION,
                    "generation": int(latest["generation"]),
                    "checksum": latest["checksum"],
                    "cursor": latest["cursor"],
                    "complete": True,
                    "fromCursor": cursor,
                    "items": [],
                    "deleted": [],
                }
            if cursor in {"0", "generation:0"}:
                return {
                    **self.catalog_snapshot(),
                    "fromCursor": cursor,
                    "requiresFullSnapshot": True,
                }
            previous = connection.execute(
                "SELECT * FROM thesis_ledger_catalog_generation WHERE cursor = ?",
                (cursor,),
            ).fetchone()
            if previous is None:
                raise ControlContractError(
                    "CATALOG_CURSOR_EXPIRED",
                    "Catalog cursor 已过期，需要完整 snapshot",
                    status_code=409,
                )
            previous_items = {
                (item["canonicalCode"], item["market"], item["instrumentType"]): item
                for item in _json_load(previous["items_json"], [])
            }
            latest_items = {
                (item["canonicalCode"], item["market"], item["instrumentType"]): item
                for item in _json_load(latest["items_json"], [])
            }
            changed = [
                latest_items[key]
                for key in sorted(latest_items)
                if previous_items.get(key) != latest_items[key]
            ]
            deleted = [
                {
                    "canonicalCode": key[0],
                    "market": key[1],
                    "instrumentType": key[2],
                }
                for key in sorted(set(previous_items) - set(latest_items))
            ]
            return {
                "contractVersion": CONTROL_CONTRACT_VERSION,
                "generation": int(latest["generation"]),
                "checksum": latest["checksum"],
                "cursor": latest["cursor"],
                "complete": True,
                "fromCursor": cursor,
                "items": changed,
                "deleted": deleted,
            }

    def catalog_ack(self, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = self.catalog_snapshot()
        if payload.get("generation") != snapshot["generation"] or payload.get("checksum") != snapshot["checksum"]:
            raise ControlContractError(
                "CATALOG_CHECKSUM_MISMATCH",
                "Catalog generation 或 checksum 不匹配",
                status_code=409,
            )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO thesis_ledger_catalog_ack
                (consumer, generation, checksum, cursor, acknowledged_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(consumer) DO UPDATE SET
                  generation=excluded.generation,
                  checksum=excluded.checksum,
                  cursor=excluded.cursor,
                  acknowledged_at=excluded.acknowledged_at
                """,
                (
                    CONSUMER_NAMESPACE,
                    snapshot["generation"],
                    snapshot["checksum"],
                    snapshot["cursor"],
                    _utc_now(),
                ),
            )
            connection.commit()
        return {"acknowledged": True, "generation": snapshot["generation"], "cursor": snapshot["cursor"]}

    def _catalog_job_owner_value(self, owner: str | None) -> str:
        value = str(owner or "").strip()
        return value[:128] if value else self._catalog_job_owner

    @staticmethod
    def _timeout_catalog_job(
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        now: str,
    ) -> sqlite3.Row:
        error = _json_load(row["error_json"], {})
        if not isinstance(error, dict):
            error = {}
        error.update(
            {
                "code": CATALOG_JOB_LEASE_EXPIRED_CODE,
                "message": "Catalog Job lease 已过期，任务可重试",
                "retryable": True,
            }
        )
        connection.execute(
            """
            UPDATE thesis_ledger_catalog_job
            SET status='timeout', error_json=?, updated_at=?
            WHERE id=? AND status='running'
            """,
            (_json(error), now, row["id"]),
        )
        return connection.execute(
            "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?",
            (row["id"],),
        ).fetchone()

    def _reclaim_expired_catalog_jobs(
        self,
        connection: sqlite3.Connection,
        now: str,
    ) -> None:
        running_jobs = connection.execute(
            "SELECT * FROM thesis_ledger_catalog_job WHERE status='running'"
        ).fetchall()
        for row in running_jobs:
            if not _lease_is_valid(row["lease_expires_at"], now):
                self._timeout_catalog_job(connection, row, now)

    def _claim_catalog_job(
        self,
        owner: str,
        *,
        initial_status: str = "pending",
    ) -> tuple[sqlite3.Row, bool]:
        """在单一 SQLite 写事务中去重并声明一个 Catalog Job。"""
        if initial_status not in {"pending", "running"}:
            raise ValueError(f"unsupported Catalog Job initial status: {initial_status}")
        job_id = str(uuid.uuid4())
        with self._schema_lock, self._connect() as connection:
            try:
                # Claim, stale-lease reclamation and de-duplication must be a
                # single SQLite writer transaction so separate API workers
                # cannot both create a running Catalog Job.
                connection.execute("BEGIN IMMEDIATE")
                now = _utc_now()
                self._reclaim_expired_catalog_jobs(connection, now)
                running = connection.execute(
                    """
                    SELECT * FROM thesis_ledger_catalog_job
                    WHERE status IN ('pending', 'running')
                    ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END,
                             created_at
                    LIMIT 1
                    """
                ).fetchone()
                if running is not None:
                    connection.commit()
                    return running, False
                latest = connection.execute(
                    """
                    SELECT generation FROM thesis_ledger_catalog_generation
                    ORDER BY generation DESC LIMIT 1
                    """
                ).fetchone()
                requested_generation = int(latest["generation"]) + 1 if latest else 1
                connection.execute(
                    """
                    INSERT INTO thesis_ledger_catalog_job
                    (id, status, generation, checksum, error_json, owner,
                     lease_expires_at, created_at, updated_at)
                    VALUES (?, ?, ?, '', NULL, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        initial_status,
                        requested_generation,
                        owner,
                        _lease_expires_at(now) if initial_status == "running" else None,
                        now,
                        now,
                    ),
                )
                connection.commit()
                row = connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?", (job_id,)
                ).fetchone()
                return row, True
            except Exception:
                connection.rollback()
                raise

    def _start_catalog_job(self, job_id: str, owner: str) -> sqlite3.Row | None:
        """Atomically move one pending Job to running and acquire its lease."""
        with self._schema_lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None
                if row["status"] != "pending":
                    connection.commit()
                    return row
                now = _utc_now()
                connection.execute(
                    """
                    UPDATE thesis_ledger_catalog_job
                    SET status='running', lease_expires_at=?, updated_at=?
                    WHERE id=? AND status='pending' AND owner=?
                    """,
                    (_lease_expires_at(now), now, job_id, owner),
                )
                connection.commit()
                return connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?",
                    (job_id,),
                ).fetchone()
            except Exception:
                connection.rollback()
                raise

    def _complete_catalog_job(
        self,
        job_id: str,
        owner: str,
        items: list[dict[str, Any]],
        failures: dict[str, str],
    ) -> sqlite3.Row:
        """校验 owner/lease 后原子发布 Catalog generation 并完成 Job。"""
        checksum = hashlib.sha256(_json(items).encode("utf-8")).hexdigest()
        with self._schema_lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?", (job_id,)
                ).fetchone()
                now = _utc_now()
                if row is None:
                    raise ControlContractError(
                        "CATALOG_JOB_NOT_FOUND",
                        "Catalog Job 不存在，无法完成任务",
                        status_code=409,
                    )
                if row["status"] != "running" or row["owner"] != owner:
                    connection.commit()
                    return row
                if not _lease_is_valid(row["lease_expires_at"], now):
                    timed_out = self._timeout_catalog_job(connection, row, now)
                    connection.commit()
                    return timed_out

                latest = connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_generation ORDER BY generation DESC LIMIT 1"
                ).fetchone()
                if latest is not None and latest["checksum"] == checksum:
                    generation = int(latest["generation"])
                    cursor = str(latest["cursor"])
                else:
                    generation = int(latest["generation"]) + 1 if latest else 1
                    cursor = f"generation:{generation}"
                    connection.execute(
                        """
                        INSERT INTO thesis_ledger_catalog_generation
                        (generation, checksum, cursor, complete, items_json, created_at)
                        VALUES (?, ?, ?, 1, ?, ?)
                        """,
                        (generation, checksum, cursor, _json(items), now),
                    )
                    connection.execute(
                        "DELETE FROM thesis_ledger_catalog_generation WHERE generation < ?",
                        (max(1, generation - 4),),
                    )
                connection.execute(
                    """
                    UPDATE thesis_ledger_catalog_job
                    SET status='succeeded', generation=?, checksum=?, error_json=?, updated_at=?
                    WHERE id=? AND owner=? AND status='running'
                    """,
                    (
                        generation,
                        checksum,
                        _json({"providerFailures": failures}) if failures else None,
                        now,
                        job_id,
                        owner,
                    ),
                )
                connection.commit()
                return connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?", (job_id,)
                ).fetchone()
            except Exception:
                connection.rollback()
                raise

    def _fail_catalog_job(
        self,
        job_id: str,
        owner: str,
        *,
        error: dict[str, Any] | None = None,
    ) -> sqlite3.Row:
        """在 owner/lease 仍有效时记录可重试的 Catalog Job 失败。"""
        with self._schema_lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?", (job_id,)
                ).fetchone()
                now = _utc_now()
                if row is None or row["status"] != "running" or row["owner"] != owner:
                    connection.commit()
                    return row
                if not _lease_is_valid(row["lease_expires_at"], now):
                    result = self._timeout_catalog_job(connection, row, now)
                else:
                    connection.execute(
                        """
                    UPDATE thesis_ledger_catalog_job
                        SET status='failed',
                            error_json=?,
                            updated_at=?
                        WHERE id=? AND owner=? AND status='running'
                        """,
                        (
                            _json(
                                error
                                or {
                                    "code": "catalog_provider_unavailable",
                                    "retryable": True,
                                }
                            ),
                            now,
                            job_id,
                            owner,
                        ),
                    )
                    result = connection.execute(
                        "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?",
                        (job_id,),
                    ).fetchone()
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    def recover_catalog_jobs(self, owner: str) -> list[tuple[str, str]]:
        """Recover pending Jobs after a process restart without duplicating work."""
        recovered: list[tuple[str, str]] = []
        with self._schema_lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                now = _utc_now()
                self._reclaim_expired_catalog_jobs(connection, now)
                timeout_rows = connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE status='timeout'"
                ).fetchall()
                for row in timeout_rows:
                    error = _json_load(row["error_json"], {})
                    if not isinstance(error, dict):
                        continue
                    if error.get("code") != CATALOG_JOB_LEASE_EXPIRED_CODE:
                        continue
                    if error.get("requeued"):
                        continue
                    active = connection.execute(
                        """
                        SELECT 1 FROM thesis_ledger_catalog_job
                        WHERE status IN ('pending', 'running') AND generation = ?
                        LIMIT 1
                        """,
                        (row["generation"],),
                    ).fetchone()
                    if active is not None:
                        continue
                    error["requeued"] = True
                    connection.execute(
                        """
                        UPDATE thesis_ledger_catalog_job
                        SET error_json=?, updated_at=?
                        WHERE id=? AND status='timeout'
                        """,
                        (_json(error), now, row["id"]),
                    )
                    requeued_id = str(uuid.uuid4())
                    connection.execute(
                        """
                        INSERT INTO thesis_ledger_catalog_job
                        (id, status, generation, checksum, error_json, owner,
                         lease_expires_at, created_at, updated_at)
                        VALUES (?, 'pending', ?, '', NULL, ?, NULL, ?, ?)
                        """,
                        (requeued_id, row["generation"], owner, now, now),
                    )
                pending_rows = connection.execute(
                    """
                    SELECT id, owner FROM thesis_ledger_catalog_job
                    WHERE status='pending'
                    ORDER BY created_at
                    """
                ).fetchall()
                connection.commit()
                recovered.extend(
                    (str(row["id"]), str(row["owner"] or owner))
                    for row in pending_rows
                )
                return recovered
            except Exception:
                connection.rollback()
                raise

    def trigger_catalog_job(self, *, owner: str | None = None) -> dict[str, Any]:
        """Create or reuse a Catalog Job without waiting for Provider I/O."""
        job_owner = self._catalog_job_owner_value(owner)
        job, claimed = self._claim_catalog_job(job_owner, initial_status="pending")
        if claimed:
            _catalog_job_manager_for(self.database_path).enqueue(
                str(job["id"]), job_owner
            )
        return self._job_payload(job)

    def get_catalog_job(self, job_id: str) -> dict[str, Any]:
        """读取一个 Catalog Job，并在读取时回收已失效的 running lease。"""
        with self._schema_lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._reclaim_expired_catalog_jobs(connection, _utc_now())
                row = connection.execute(
                    "SELECT * FROM thesis_ledger_catalog_job WHERE id = ?",
                    (job_id,),
                ).fetchone()
                if row is None:
                    connection.rollback()
                    raise ControlContractError(
                        "CATALOG_JOB_NOT_FOUND",
                        "Catalog Job 不存在",
                        status_code=404,
                    )
                connection.commit()
                return self._job_payload(row)
            except ControlContractError:
                raise
            except Exception:
                connection.rollback()
                raise

    def execute_catalog_job(self, job_id: str, owner: str) -> dict[str, Any]:
        """Run one claimed Job; called by the process-local Catalog worker."""
        started = self._start_catalog_job(job_id, owner)
        if started is None:
            raise ControlContractError(
                "CATALOG_JOB_NOT_FOUND",
                "Catalog Job 不存在，无法执行",
                status_code=409,
            )
        if started["status"] != "running" or started["owner"] != owner:
            return self._job_payload(started)
        try:
            fixture_mode = os.getenv("THESIS_LEDGER_FIXTURE_MODE", "false").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            failures: dict[str, str] = {}
            if fixture_mode:
                items = list(DEFAULT_CATALOG)
            else:
                from src.services.thesis_ledger_catalog import build_catalog

                items, failures = build_catalog()
            items = sorted(
                items,
                key=lambda item: (
                    item["canonicalCode"],
                    item["market"],
                    item["instrumentType"],
                ),
            )
            completed = self._complete_catalog_job(job_id, owner, items, failures)
            return self._job_payload(completed)
        except Exception as exc:
            failure = {
                "code": str(getattr(exc, "code", "catalog_provider_unavailable")),
                "retryable": bool(getattr(exc, "retryable", True)),
            }
            if hasattr(exc, "code"):
                failure["message"] = str(exc)
            failed = self._fail_catalog_job(job_id, owner, error=failure)
            if failed is None:
                raise
            return self._job_payload(failed)

    @staticmethod
    def _job_payload(row: sqlite3.Row, *, now: str | None = None) -> dict[str, Any]:
        status = str(row["status"])
        current_time = now or _utc_now()
        return {
            "id": row["id"],
            "status": status,
            "generation": int(row["generation"]),
            "checksum": row["checksum"],
            "error": _json_load(row["error_json"], None),
            "owner": row["owner"],
            "leaseExpiresAt": row["lease_expires_at"],
            "leaseValid": status == "running"
            and _lease_is_valid(row["lease_expires_at"], current_time),
            "retryable": status in {"failed", "timeout"},
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }


class _CatalogJobManager:
    """One bounded process-local queue for manual and scheduled Catalog triggers."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self.owner = f"catalog-worker:pid:{os.getpid()}:{uuid.uuid4().hex}"
        self._queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="thesis-ledger-catalog-worker",
            daemon=True,
        )
        self._thread.start()
        self._recover()

    def enqueue(self, job_id: str, owner: str) -> None:
        """将已声明的 Catalog Job 放入当前进程的有界工作流。"""
        self._queue.put((job_id, owner))

    def _recover(self) -> None:
        """恢复 pending 与 lease 过期后可重试的 Job。"""
        store = ThesisLedgerControlStore(self.database_path)
        for job_id, owner in store.recover_catalog_jobs(self.owner):
            self.enqueue(job_id, owner)

    def _run(self) -> None:
        """持续消费 Job，并让 Job 自身记录稳定失败状态。"""
        store = ThesisLedgerControlStore(self.database_path)
        while True:
            job_id, owner = self._queue.get()
            try:
                store.execute_catalog_job(job_id, owner)
            except Exception:  # noqa: BLE001 - the Job itself records stable failure state.
                logger.exception("Catalog worker failed job=%s", job_id)
            finally:
                self._queue.task_done()


_catalog_job_managers: dict[str, _CatalogJobManager] = {}
_catalog_job_managers_lock = threading.Lock()


def _catalog_job_manager_for(database_path: str) -> _CatalogJobManager:
    with _catalog_job_managers_lock:
        manager = _catalog_job_managers.get(database_path)
        if manager is None:
            manager = _CatalogJobManager(database_path)
            _catalog_job_managers[database_path] = manager
        return manager
