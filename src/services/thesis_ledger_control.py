# -*- coding: utf-8 -*-
"""ThesisLedger 当前 Control 合同的 DSA 侧状态与校验。

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
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.services.provider_credentials import (
    CredentialValue,
    configured_fields,
    decode_credential_plaintext,
    encode_credential_plaintext,
    merge_credential_patch,
    parse_credential_patch,
    validate_config_patch_keys,
    validate_stored_credential,
)
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_manifest import build_provider_manifest as _manifest
from src.services.provider_oauth_contract import OAuthStateError, validate_oauth_token
from src.services.thesis_ledger_akshare_manifest import build_akshare_manifest
from src.services.thesis_ledger_efinance_manifest import build_efinance_manifest
from src.services.thesis_ledger_event_v3_adapters import event_adapter_matches
from src.services.thesis_ledger_market_v3_adapters import (
    HITHINK_STOCK_HISTORY_SOURCE,
    market_v3_bar_adapter_reason,
)
from src.services.thesis_ledger_market_v3_facts import HITHINK_ETF_HISTORY_SOURCE
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_SOURCE,
)
from src.services.thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE,
    HITHINK_STOCK_SNAPSHOT_SOURCE,
)
from src.services.thesis_ledger_price_route_access import policy_route_admission_reason
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from src.services.thesis_ledger_route_admission_v3 import (
    canonical_route_key_json,
    normalize_scope_symbols,
)

logger = logging.getLogger(__name__)

CONTROL_CONTRACT_V3_VERSION = 3
CONSUMER_NAMESPACE = "thesis-ledger"
CATALOG_JOB_LEASE_SECONDS = 300
CATALOG_JOB_LEASE_EXPIRED_CODE = "CATALOG_JOB_LEASE_EXPIRED"
PROVIDER_REQUEST_BUDGET_SECONDS = 600

INSTRUMENT_TYPES = (
    "STOCK",
    "ETF",
    "MUTUAL_FUND",
    "LOF",
    "INDEX",
    "BOND",
    "CONVERTIBLE_BOND",
)


def _hithink_manifest() -> dict[str, Any]:
    manifest = _manifest(
        "hithink",
        "HiThink",
        {
            "REALTIME_QUOTE": ("ETF", "STOCK"),
            "DAILY_BAR": ("ETF", "STOCK"),
            "CASH_DISTRIBUTION": ("ETF",),
        },
        requires_credential=True,
        upstream_sources=(
            (HITHINK_ETF_HISTORY_SOURCE, "HiThink ETF 历史接口"),
            (HITHINK_STOCK_HISTORY_SOURCE, "HiThink 股票历史接口"),
            (HITHINK_STOCK_SNAPSHOT_SOURCE, "HiThink A股行情快照"),
            (HITHINK_ETF_SNAPSHOT_SOURCE, "HiThink ETF/LOF 场内快照"),
            (HITHINK_DIVIDEND_SOURCE, "HiThink 场内基金分红"),
        ),
        markets=("CN",),
        configuration_mode="dsa_environment",
    )
    # The ETF and stock endpoints have independent adapter and admission
    # evidence. Do not inherit the provider-wide union for either source.
    source_capabilities = {
        HITHINK_ETF_HISTORY_SOURCE: {"DAILY_BAR": ["ETF"]},
        HITHINK_STOCK_HISTORY_SOURCE: {"DAILY_BAR": ["STOCK"]},
        HITHINK_STOCK_SNAPSHOT_SOURCE: {"REALTIME_QUOTE": ["STOCK"]},
        HITHINK_ETF_SNAPSHOT_SOURCE: {"REALTIME_QUOTE": ["ETF"]},
        HITHINK_DIVIDEND_SOURCE: {"CASH_DISTRIBUTION": ["ETF"]},
    }
    for source in manifest["upstreamSources"]:
        source["capabilities"] = source_capabilities[source["sourceId"]]
    manifest["credentialEnvironmentKey"] = "HITHINK_API_KEY"
    return manifest


PROVIDER_MANIFESTS: dict[str, dict[str, Any]] = {
    "akshare": build_akshare_manifest(),
    "efinance": build_efinance_manifest(),
    "tencent": _manifest(
        "tencent",
        "腾讯财经",
        {
            "DAILY_BAR": ("STOCK", "ETF"),
        },
        upstream_sources=(("tencent", "腾讯财经"),),
    ),
    "tushare": _manifest(
        "tushare",
        "Tushare Pro",
        {
            "REALTIME_QUOTE": ("STOCK",),
            "DAILY_BAR": ("STOCK", "ETF"),
            "CHIP_SUMMARY": ("STOCK",),
        },
        requires_credential=True,
        markets=("CN", "HK"),
        configuration_mode="dsa_environment",
    ),
    "tickflow": _manifest(
        "tickflow",
        "TickFlow",
        {
            "REALTIME_QUOTE": ("STOCK",),
            "DAILY_BAR": ("STOCK",),
        },
        requires_credential=True,
        markets=("CN",),
        configuration_mode="dsa_environment",
    ),
    "pytdx": _manifest(
        "pytdx",
        "通达信（pytdx）",
        {
            "REALTIME_QUOTE": ("STOCK",),
            "DAILY_BAR": ("STOCK",),
        },
        markets=("CN",),
        configuration_mode="built_in",
    ),
    "baostock": _manifest(
        "baostock",
        "BaoStock",
        {"DAILY_BAR": ("STOCK",)},
        markets=("CN",),
        configuration_mode="built_in",
    ),
    "yfinance": _manifest(
        "yfinance",
        "Yahoo Finance",
        {
            "REALTIME_QUOTE": ("STOCK", "ETF", "INDEX"),
            "DAILY_BAR": ("STOCK", "ETF", "INDEX"),
        },
        markets=("CN", "HK", "US", "JP", "KR", "TW"),
        configuration_mode="built_in",
    ),
    "longbridge": _manifest(
        "longbridge",
        "Longbridge",
        {
            "REALTIME_QUOTE": ("STOCK", "ETF"),
            "DAILY_BAR": ("STOCK", "ETF"),
        },
        requires_credential=True,
        markets=("HK", "US"),
        configuration_mode="dsa_environment",
    ),
    "finnhub": _manifest(
        "finnhub",
        "Finnhub",
        {
            "REALTIME_QUOTE": ("STOCK",),
            "DAILY_BAR": ("STOCK",),
        },
        requires_credential=True,
        markets=("US",),
        configuration_mode="dsa_environment",
    ),
    "alphavantage": _manifest(
        "alphavantage",
        "Alpha Vantage",
        {
            "REALTIME_QUOTE": ("STOCK",),
            "DAILY_BAR": ("STOCK",),
        },
        requires_credential=True,
        markets=("US",),
        configuration_mode="dsa_environment",
    ),
    "hithink": _hithink_manifest(),
    "rqdata": _manifest("rqdata", "RQData", {}, requires_credential=True),
}


def _provider_configured(
    manifest: dict[str, Any],
    config: sqlite3.Row | None,
) -> bool:
    if not manifest.get("requiresCredential", False):
        return True
    _, _, configured, _ = _credential_state(manifest, config)
    return configured


def _environment_credential_values(provider_id: str) -> dict[str, str]:
    if provider_id == "hithink":
        api_key = os.environ.get("HITHINK_API_KEY", "").strip()
        return {"apiKey": api_key} if api_key else {}
    try:
        from src.config import get_config

        runtime_config = get_config()
        if provider_id == "longbridge":
            from data_provider.longbridge_fetcher import _longbridge_credentials

            credentials = _longbridge_credentials(runtime_config)
            return {
                field: str(value).strip()
                for field, value in {
                    "appKey": credentials.get("app_key"),
                    "appSecret": credentials.get("app_secret"),
                    "accessToken": credentials.get("access_token"),
                    "oauthClientId": credentials.get("oauth_client_id"),
                }.items()
                if value
            }
        credential_fields = {
            "tushare": {"token": "tushare_token"},
            "tickflow": {"apiKey": "tickflow_api_key"},
            "finnhub": {"apiKey": "finnhub_api_key"},
            "alphavantage": {"apiKey": "alphavantage_api_key"},
        }
        return {
            field: str(getattr(runtime_config, env_name, "") or "").strip()
            for field, env_name in credential_fields.get(provider_id, {}).items()
            if str(getattr(runtime_config, env_name, "") or "").strip()
        }
    except Exception:
        return {}


def _environment_credential_state(provider_id: str) -> tuple[dict[str, bool], bool]:
    values = _environment_credential_values(provider_id)
    if provider_id == "longbridge":
        try:
            from src.config import get_config
            from data_provider.longbridge_fetcher import LongbridgeFetcher

            configured = LongbridgeFetcher.has_configured_credentials(get_config())
        except Exception:
            configured = False
        return {
            "appKey": bool(values.get("appKey")),
            "appSecret": bool(values.get("appSecret")),
            "accessToken": bool(values.get("accessToken")),
        }, configured
    return {field: bool(value) for field, value in values.items()}, bool(values)


def _credential_state(
    manifest: dict[str, Any],
    config: sqlite3.Row | None,
) -> tuple[str, dict[str, bool], bool, str | None]:
    if not manifest.get("requiresCredential", False):
        return "built_in", {}, bool(config and config["credential_ciphertext"]), None
    provider_id = str(manifest["providerId"])
    if config and config["credential_ciphertext"]:
        try:
            plaintext = _decrypt_secret(
                str(config["secret_key_version"] or ""),
                str(config["credential_ciphertext"]),
            )
            credential = decode_credential_plaintext(plaintext)
            validate_stored_credential(provider_id, credential)
            if credential.method == "oauth":
                validate_oauth_token(
                    credential.values.get("clientId", ""),
                    credential.values.get("tokenJson", ""),
                )
            return "control", configured_fields(provider_id, credential), True, credential.method
        except Exception:
            # A present but unreadable page value blocks environment fallback.
            return "control", configured_fields(provider_id, None), False, None
    _, environment_configured = _environment_credential_state(provider_id)
    if environment_configured:
        return "environment", configured_fields(provider_id, None), True, None
    return "none", configured_fields(provider_id, None), False, None


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
        contract_version: int = CONTROL_CONTRACT_V3_VERSION,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.request_id = request_id or str(uuid.uuid4())
        self.details = details or {}
        self.contract_version = contract_version

    def detail(self) -> dict[str, Any]:
        return {
            "contractVersion": self.contract_version,
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


def _provider_credential_revision_from_snapshot(
    snapshot: ProviderCredentialSnapshot,
) -> str | None:
    """Return an internal, purpose-separated revision for environment credentials.

    The result is suitable only for comparing against internal route-admission evidence.
    It must not be included in registry/API responses, logs, or exception messages.
    """

    from src.services.provider_credential_revision import provider_credential_revision

    try:
        master_version, master_key = _secret_key()
    except ControlContractError:
        return None

    return provider_credential_revision(snapshot, master_version, master_key)


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


def normalize_policy_v3(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize the versioned, dimensioned V3 route policy."""

    def fail(code: str, message: str, request_id: str = "") -> None:
        raise ControlContractError(
            code,
            message,
            request_id=request_id,
            contract_version=CONTROL_CONTRACT_V3_VERSION,
        )

    if not isinstance(payload, dict):
        fail("INVALID_POLICY_SCHEMA", "Policy 必须是 JSON 对象")
    request_id_value = payload.get("requestId")
    request_id = request_id_value.strip() if isinstance(request_id_value, str) else ""
    if set(payload) != {
        "contractVersion",
        "consumer",
        "requestId",
        "revision",
        "enabled",
        "routes",
    }:
        fail("INVALID_POLICY_SCHEMA", "V3 Policy 字段不符合严格契约", request_id)
    if payload.get("contractVersion") != CONTROL_CONTRACT_V3_VERSION:
        fail("CONTROL_CONTRACT_UNSUPPORTED", "Control Contract V3 版本不兼容", request_id)
    if payload.get("consumer") != CONSUMER_NAMESPACE:
        fail("INVALID_CONSUMER", "Control Contract consumer namespace 不正确", request_id)
    if not request_id:
        fail("INVALID_POLICY_SCHEMA", "requestId 必须是非空字符串", request_id)
    revision = payload.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        fail("INVALID_REVISION", "Policy revision 必须是正整数", request_id)
    enabled = payload.get("enabled")
    if not isinstance(enabled, bool):
        fail("INVALID_POLICY_SCHEMA", "Policy enabled 必须是布尔值", request_id)

    routes = payload.get("routes")
    if not isinstance(routes, list):
        fail("INVALID_POLICY_SCHEMA", "V3 routes 必须是数组", request_id)
    normalized_routes: list[dict[str, Any]] = []
    seen_routes: set[tuple[Any, ...]] = set()
    valid_asset_types = set(INSTRUMENT_TYPES)
    for route in routes:
        if not isinstance(route, dict) or set(route) != {"key", "targets"}:
            fail("INVALID_POLICY_SCHEMA", "V3 route 必须只包含 key 和 targets", request_id)
        key = route.get("key")
        if not isinstance(key, dict):
            fail("INVALID_POLICY_SCHEMA", "V3 route key 必须是对象", request_id)
        kind = key.get("kind")
        market = key.get("market")
        asset_type = key.get("assetType")
        if (
            not isinstance(market, str)
            or market not in {"CN", "HK", "US"}
            or not isinstance(asset_type, str)
            or asset_type not in valid_asset_types
        ):
            fail("INVALID_POLICY_SCHEMA", "V3 route key 的 market 或 assetType 不受支持", request_id)
        if kind == "bar":
            if set(key) != {"kind", "market", "assetType", "capability", "timeframe", "adjustment"}:
                fail("INVALID_POLICY_SCHEMA", "V3 bar key 字段不符合严格契约", request_id)
            capability = key.get("capability")
            timeframe = key.get("timeframe")
            adjustment = key.get("adjustment")
            if not isinstance(capability, str) or capability not in {"DAILY_BAR", "MINUTE_BAR"}:
                fail("INVALID_POLICY_SCHEMA", "V3 bar capability 不受支持", request_id)
            if not isinstance(timeframe, str) or not isinstance(adjustment, str):
                fail("INVALID_POLICY_SCHEMA", "V3 timeframe 或 adjustment 不受支持", request_id)
            if (capability, timeframe) not in {("DAILY_BAR", "1d"), ("MINUTE_BAR", "1m")}:
                fail("INVALID_POLICY_SCHEMA", "V3 bar capability 与 timeframe 不匹配", request_id)
            if adjustment not in {"none", "qfq", "hfq"}:
                fail("INVALID_POLICY_SCHEMA", "V3 adjustment 不受支持", request_id)
            normalized_key = {
                "kind": kind,
                "market": market,
                "assetType": asset_type,
                "capability": capability,
                "timeframe": timeframe,
                "adjustment": adjustment,
            }
            route_identity = (kind, market, asset_type, capability, timeframe, adjustment)
        elif kind == "data":
            if set(key) != {"kind", "market", "assetType", "capability"}:
                fail("INVALID_POLICY_SCHEMA", "V3 data key 字段不符合严格契约", request_id)
            capability_value = key.get("capability")
            if not isinstance(capability_value, str) or not capability_value.strip():
                fail("INVALID_POLICY_SCHEMA", "V3 data capability 必须是非空字符串", request_id)
            capability = capability_value.strip()
            if capability in {"DAILY_BAR", "MINUTE_BAR"}:
                fail("INVALID_POLICY_SCHEMA", "行情能力必须使用带周期和价格口径的 bar key", request_id)
            normalized_key = {
                "kind": kind,
                "market": market,
                "assetType": asset_type,
                "capability": capability,
            }
            route_identity = (kind, market, asset_type, capability)
        else:
            fail("INVALID_POLICY_SCHEMA", "V3 route key kind 不受支持", request_id)
        if route_identity in seen_routes:
            fail("INVALID_POLICY_SCHEMA", "V3 routes 中存在重复维度", request_id)
        seen_routes.add(route_identity)

        raw_targets = route.get("targets")
        if not isinstance(raw_targets, list) or not 1 <= len(raw_targets) <= 2:
            fail("INVALID_ROUTE_TARGETS", "每条 V3 route 必须有一至两个 RouteTarget", request_id)
        normalized_targets: list[dict[str, str]] = []
        seen_targets: set[tuple[str, str]] = set()
        for target in raw_targets:
            if not isinstance(target, dict) or set(target) != {"providerId", "upstreamSource"}:
                fail("INVALID_ROUTE_TARGET", "V3 RouteTarget 字段不符合严格契约", request_id)
            provider_value = target.get("providerId")
            source_value = target.get("upstreamSource")
            if not isinstance(provider_value, str) or not isinstance(source_value, str):
                fail("INVALID_ROUTE_TARGET", "V3 RouteTarget 标识必须是字符串", request_id)
            provider_id = provider_value
            upstream_source = source_value
            if not provider_id.strip() or not upstream_source.strip():
                fail("INVALID_ROUTE_TARGET", "V3 RouteTarget 标识不能为空", request_id)
            target_identity = (provider_id, upstream_source)
            if target_identity in seen_targets:
                fail("INVALID_ROUTE_TARGETS", "V3 RouteTarget 不能重复", request_id)
            seen_targets.add(target_identity)
            normalized_targets.append(
                {"providerId": provider_id, "upstreamSource": upstream_source}
            )
        normalized_routes.append({"key": normalized_key, "targets": normalized_targets})

    return {
        "contractVersion": CONTROL_CONTRACT_V3_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "requestId": request_id,
        "revision": revision,
        "enabled": enabled,
        "routes": normalized_routes,
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
                CREATE TABLE IF NOT EXISTS thesis_ledger_route_admission_v3 (
                    consumer TEXT NOT NULL,
                    route_key_json TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    upstream_source TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN ('admitted', 'invalid', 'revoked')),
                    evidence_ref TEXT NOT NULL,
                    evidence_sha256 TEXT NOT NULL,
                    scope_symbols_json TEXT NOT NULL,
                    scope_date_from TEXT NOT NULL,
                    scope_date_to TEXT NOT NULL,
                    adapter_revision TEXT NOT NULL,
                    source_revision TEXT NOT NULL,
                    credential_revision TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_until TEXT NOT NULL,
                    recorded_by TEXT NOT NULL,
                    record_version INTEGER NOT NULL DEFAULT 1,
                    recorded_at TEXT NOT NULL,
                    invalidated_at TEXT,
                    invalidation_reason TEXT,
                    PRIMARY KEY (consumer, route_key_json, provider_id, upstream_source)
                );
                CREATE INDEX IF NOT EXISTS thesis_ledger_route_admission_v3_validity_idx
                    ON thesis_ledger_route_admission_v3 (consumer, status, valid_until);
                CREATE TABLE IF NOT EXISTS thesis_ledger_provider_config (
                    provider_id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    settings_json TEXT NOT NULL DEFAULT '{}',
                    credential_ciphertext TEXT,
                    secret_key_version TEXT,
                    config_version INTEGER NOT NULL DEFAULT 0,
                    credential_version INTEGER NOT NULL DEFAULT 0,
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
                CREATE TABLE IF NOT EXISTS thesis_ledger_provider_request_budget (
                    request_key TEXT PRIMARY KEY,
                    provider_id TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    instrument_type TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    request_id TEXT,
                    attempted_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS thesis_ledger_provider_request_budget_expiry_idx
                    ON thesis_ledger_provider_request_budget (expires_at);
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
            provider_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(thesis_ledger_provider_config)"
                ).fetchall()
            }
            if "config_version" not in provider_columns:
                connection.execute(
                    "ALTER TABLE thesis_ledger_provider_config "
                    "ADD COLUMN config_version INTEGER NOT NULL DEFAULT 0"
                )
            if "credential_version" not in provider_columns:
                connection.execute(
                    "ALTER TABLE thesis_ledger_provider_config "
                    "ADD COLUMN credential_version INTEGER NOT NULL DEFAULT 0"
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

    @staticmethod
    def _route_admission_identity_v3(
        key: dict[str, Any], target: dict[str, Any], consumer: str = CONSUMER_NAMESPACE
    ) -> tuple[str, str, str, str]:
        if consumer != CONSUMER_NAMESPACE:
            raise ValueError("Unsupported RouteAdmission consumer")
        if not isinstance(target, dict) or set(target) != {"providerId", "upstreamSource"}:
            raise ValueError("RouteTarget must contain providerId and upstreamSource")
        provider_id = target.get("providerId")
        upstream_source = target.get("upstreamSource")
        if not all(isinstance(value, str) and value.strip() for value in (provider_id, upstream_source)):
            raise ValueError("RouteTarget identity values must be non-empty strings")
        return (
            consumer,
            canonical_route_key_json(key),
            provider_id.strip().lower(),
            upstream_source.strip().lower(),
        )

    @staticmethod
    def _route_admission_payload_v3(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "consumer": str(row["consumer"]),
            "routeKey": _json_load(row["route_key_json"], {}),
            "target": {
                "providerId": str(row["provider_id"]),
                "upstreamSource": str(row["upstream_source"]),
            },
            "status": str(row["status"]),
            "evidenceRef": str(row["evidence_ref"]),
            "evidenceSha256": str(row["evidence_sha256"]),
            "scopeSymbols": _json_load(row["scope_symbols_json"], []),
            "scopeDateFrom": str(row["scope_date_from"]),
            "scopeDateTo": str(row["scope_date_to"]),
            "adapterRevision": str(row["adapter_revision"]),
            "sourceRevision": str(row["source_revision"]),
            "credentialRevision": str(row["credential_revision"]),
            "validFrom": str(row["valid_from"]),
            "validUntil": str(row["valid_until"]),
            "recordedBy": str(row["recorded_by"]),
            "recordVersion": int(row["record_version"]),
            "recordedAt": str(row["recorded_at"]),
            "invalidatedAt": row["invalidated_at"],
            "invalidationReason": row["invalidation_reason"],
        }

    @staticmethod
    def _route_admission_state_v3(
        admission: dict[str, Any] | None, *, now: str | None = None
    ) -> str:
        if admission is None:
            return "pending"
        status = admission.get("status")
        if status != "admitted":
            return str(status)
        current = _parse_utc(now or _utc_now())
        valid_from = _parse_utc(admission.get("validFrom"))
        valid_until = _parse_utc(admission.get("validUntil"))
        if current is None or valid_from is None or valid_until is None:
            return "invalid"
        if current < valid_from:
            return "not_yet_valid"
        if current >= valid_until:
            return "expired"
        return "admitted"

    def record_route_admission_v3(
        self,
        *,
        key: dict[str, Any],
        target: dict[str, Any],
        evidence_ref: str,
        evidence_sha256: str,
        scope_symbols: list[str],
        scope_date_from: str,
        scope_date_to: str,
        adapter_revision: str,
        source_revision: str,
        credential_revision: str,
        valid_from: str,
        valid_until: str,
        recorded_by: str,
        consumer: str = CONSUMER_NAMESPACE,
    ) -> dict[str, Any]:
        """Explicitly record one evidenced RouteKey × RouteTarget admission.

        This method is intentionally not exposed by the Control HTTP API. Missing
        rows remain pending; routine configuration and health updates never write
        admission evidence.
        """
        identity = self._route_admission_identity_v3(key, target, consumer)
        symbols = normalize_scope_symbols(scope_symbols)
        if not isinstance(scope_date_from, str) or not isinstance(scope_date_to, str):
            raise ValueError("scope date boundaries must be ISO dates")
        try:
            range_start = date.fromisoformat(scope_date_from)
            range_end = date.fromisoformat(scope_date_to)
        except ValueError as error:
            raise ValueError("scope date boundaries must be ISO dates") from error
        if range_start.isoformat() != scope_date_from or range_end.isoformat() != scope_date_to:
            raise ValueError("scope date boundaries must be canonical ISO dates")
        if range_start > range_end:
            raise ValueError("scope date range is inverted")

        if not isinstance(evidence_ref, str) or not evidence_ref.strip():
            raise ValueError("evidence_ref is required")
        normalized_hash = evidence_sha256.strip().lower() if isinstance(evidence_sha256, str) else ""
        if len(normalized_hash) != 64:
            raise ValueError("evidence_sha256 must be a SHA-256 hex digest")
        try:
            bytes.fromhex(normalized_hash)
        except ValueError as error:
            raise ValueError("evidence_sha256 must be a SHA-256 hex digest") from error

        revisions = (adapter_revision, source_revision, credential_revision)
        if not all(isinstance(value, str) and value.strip() for value in revisions):
            raise ValueError("adapter, source, and credential revisions are required")
        if not isinstance(recorded_by, str) or not recorded_by.strip():
            raise ValueError("recorded_by is required")

        def normalized_instant(value: str, field: str) -> str:
            if not isinstance(value, str):
                raise ValueError(f"{field} must be a timezone-aware ISO timestamp")
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError as error:
                raise ValueError(f"{field} must be a timezone-aware ISO timestamp") from error
            if parsed.tzinfo is None:
                raise ValueError(f"{field} must include a timezone")
            return parsed.astimezone(timezone.utc).isoformat()

        starts_at = normalized_instant(valid_from, "valid_from")
        expires_at = normalized_instant(valid_until, "valid_until")
        if _parse_utc(starts_at) >= _parse_utc(expires_at):
            raise ValueError("valid_until must be later than valid_from")

        values = (
            *identity,
            "admitted",
            evidence_ref.strip(),
            normalized_hash,
            _json(symbols),
            scope_date_from,
            scope_date_to,
            adapter_revision.strip(),
            source_revision.strip(),
            credential_revision.strip(),
            starts_at,
            expires_at,
            recorded_by.strip(),
            _utc_now(),
        )
        with self._schema_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM thesis_ledger_route_admission_v3
                WHERE consumer = ? AND route_key_json = ? AND provider_id = ? AND upstream_source = ?
                """,
                identity,
            ).fetchone()
            if existing is not None:
                existing_payload = self._route_admission_payload_v3(existing)
                unchanged = all(
                    (
                        existing_payload["status"] == "admitted",
                        existing_payload["evidenceRef"] == values[5],
                        existing_payload["evidenceSha256"] == values[6],
                        existing_payload["scopeSymbols"] == symbols,
                        existing_payload["scopeDateFrom"] == scope_date_from,
                        existing_payload["scopeDateTo"] == scope_date_to,
                        existing_payload["adapterRevision"] == adapter_revision.strip(),
                        existing_payload["sourceRevision"] == source_revision.strip(),
                        existing_payload["credentialRevision"] == credential_revision.strip(),
                        existing_payload["validFrom"] == starts_at,
                        existing_payload["validUntil"] == expires_at,
                        existing_payload["recordedBy"] == recorded_by.strip(),
                    )
                )
                if unchanged:
                    connection.commit()
                    return existing_payload

            connection.execute(
                """
                INSERT INTO thesis_ledger_route_admission_v3 (
                    consumer, route_key_json, provider_id, upstream_source, status,
                    evidence_ref, evidence_sha256, scope_symbols_json,
                    scope_date_from, scope_date_to, adapter_revision, source_revision,
                    credential_revision, valid_from, valid_until, recorded_by,
                    record_version, recorded_at, invalidated_at, invalidation_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL)
                ON CONFLICT (consumer, route_key_json, provider_id, upstream_source)
                DO UPDATE SET
                    status=excluded.status,
                    evidence_ref=excluded.evidence_ref,
                    evidence_sha256=excluded.evidence_sha256,
                    scope_symbols_json=excluded.scope_symbols_json,
                    scope_date_from=excluded.scope_date_from,
                    scope_date_to=excluded.scope_date_to,
                    adapter_revision=excluded.adapter_revision,
                    source_revision=excluded.source_revision,
                    credential_revision=excluded.credential_revision,
                    valid_from=excluded.valid_from,
                    valid_until=excluded.valid_until,
                    recorded_by=excluded.recorded_by,
                    record_version=thesis_ledger_route_admission_v3.record_version + 1,
                    recorded_at=excluded.recorded_at,
                    invalidated_at=NULL,
                    invalidation_reason=NULL
                """,
                values,
            )
            row = connection.execute(
                """
                SELECT * FROM thesis_ledger_route_admission_v3
                WHERE consumer = ? AND route_key_json = ? AND provider_id = ? AND upstream_source = ?
                """,
                identity,
            ).fetchone()
            connection.commit()
        return self._route_admission_payload_v3(row)

    def get_route_admission_v3(
        self,
        *,
        key: dict[str, Any],
        target: dict[str, Any],
        consumer: str = CONSUMER_NAMESPACE,
        now: str | None = None,
    ) -> dict[str, Any] | None:
        identity = self._route_admission_identity_v3(key, target, consumer)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM thesis_ledger_route_admission_v3
                WHERE consumer = ? AND route_key_json = ? AND provider_id = ? AND upstream_source = ?
                """,
                identity,
            ).fetchone()
        if row is None:
            return None
        payload = self._route_admission_payload_v3(row)
        payload["admissionState"] = self._route_admission_state_v3(payload, now=now)
        return payload

    def _set_route_admission_state_v3(
        self,
        *,
        key: dict[str, Any],
        target: dict[str, Any],
        status: str,
        reason: str,
        consumer: str = CONSUMER_NAMESPACE,
    ) -> dict[str, Any] | None:
        if status not in {"invalid", "revoked"}:
            raise ValueError("RouteAdmission state must be invalid or revoked")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("RouteAdmission invalidation reason is required")
        identity = self._route_admission_identity_v3(key, target, consumer)
        with self._schema_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE thesis_ledger_route_admission_v3
                SET status = ?, invalidated_at = ?, invalidation_reason = ?,
                    record_version = record_version + 1
                WHERE consumer = ? AND route_key_json = ? AND provider_id = ? AND upstream_source = ?
                """,
                (status, _utc_now(), reason.strip(), *identity),
            )
            row = connection.execute(
                """
                SELECT * FROM thesis_ledger_route_admission_v3
                WHERE consumer = ? AND route_key_json = ? AND provider_id = ? AND upstream_source = ?
                """,
                identity,
            ).fetchone()
            connection.commit()
        if row is None:
            return None
        payload = self._route_admission_payload_v3(row)
        payload["admissionState"] = status
        return payload

    def invalidate_route_admission_v3(
        self,
        *,
        key: dict[str, Any],
        target: dict[str, Any],
        reason: str,
        consumer: str = CONSUMER_NAMESPACE,
    ) -> dict[str, Any] | None:
        return self._set_route_admission_state_v3(
            key=key, target=target, status="invalid", reason=reason, consumer=consumer
        )

    def revoke_route_admission_v3(
        self,
        *,
        key: dict[str, Any],
        target: dict[str, Any],
        reason: str,
        consumer: str = CONSUMER_NAMESPACE,
    ) -> dict[str, Any] | None:
        return self._set_route_admission_state_v3(
            key=key, target=target, status="revoked", reason=reason, consumer=consumer
        )

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
        *,
        upstream_source: str | None = None,
    ) -> sqlite3.Row | None:
        scope_key = ":".join(
            (CONSUMER_NAMESPACE, provider_id, capability, instrument_type, upstream_source)
        ) if upstream_source else ":".join(
            (CONSUMER_NAMESPACE, provider_id, capability, instrument_type)
        )
        return connection.execute(
            "SELECT * FROM thesis_ledger_provider_health WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()

    def _v3_target_reason(
        self,
        connection: sqlite3.Connection,
        policy: dict[str, Any],
        key: dict[str, Any],
        target: dict[str, str],
        configurations: dict[str, sqlite3.Row],
    ) -> str | None:
        provider_id = target["providerId"].strip().lower()
        upstream_source = target["upstreamSource"].strip().lower()
        manifest = PROVIDER_MANIFESTS.get(provider_id)
        if not manifest or key["market"] not in manifest.get("markets", []):
            return "not_adapted"
        source_manifest = next(
            (
                source
                for source in manifest.get("upstreamSources", [])
                if str(source.get("sourceId") or "").strip().lower() == upstream_source
            ),
            None,
        )
        if source_manifest is None:
            return "not_adapted"
        capability = key["capability"]
        asset_type = key["assetType"]
        if (
            key.get("kind") == "data" and capability == "REALTIME_QUOTE"
            and asset_type == "ETF" and provider_id == "akshare"
        ):
            return "not_adapted"
        is_event = event_adapter_matches(key, target)
        if (
            key.get("kind") == "data" and not is_event
            and provider_id != "hithink"
            and current_data_adapter_revisions(key, target) is None
        ):
            return "not_adapted"
        if not is_event and asset_type not in source_manifest.get("capabilities", {}).get(capability, []):
            return "not_adapted"
        adapter_reason = (
            market_v3_bar_adapter_reason(key, target)
            if key.get("kind") == "bar" else None
        )
        if adapter_reason is not None:
            return adapter_reason
        if not policy["enabled"]:
            return "disabled"
        config = configurations.get(provider_id)
        if config is not None and not bool(config["enabled"]):
            return "disabled"
        if not _provider_configured(manifest, config):
            return "credential_missing"
        admission_reason = policy_route_admission_reason(
            self, connection, key, target, policy["consumer"], manifest,
        )
        if admission_reason is not None:
            return admission_reason
        health = self._health(
            connection,
            provider_id,
            capability,
            asset_type,
            upstream_source=upstream_source,
        )
        if health and str(health["circuit"] or "closed") == "open":
            return "upstream_failure"
        return None

    def _effective_v3(
        self,
        connection: sqlite3.Connection,
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        configurations = self._configuration(connection)
        effective_routes: list[dict[str, Any]] = []
        for route in policy["routes"]:
            key = route["key"]
            targets = []
            for index, target in enumerate(route["targets"]):
                reason = self._v3_target_reason(
                    connection, policy, key, target, configurations
                )
                targets.append(
                    {
                        **target,
                        "routeIndex": index,
                        "eligible": reason is None,
                        "reason": reason,
                    }
                )
            route_reason = None
            if not any(target["eligible"] for target in targets):
                route_reason = str(targets[0]["reason"] or "not_adapted")
            effective_routes.append(
                {"key": key, "targets": targets, "reason": route_reason}
            )
        return {
            "contractVersion": CONTROL_CONTRACT_V3_VERSION,
            "consumer": CONSUMER_NAMESPACE,
            "requestId": policy["requestId"],
            "revision": policy["revision"],
            "sourceDesiredRevision": policy["revision"],
            "enabled": policy["enabled"],
            "routes": effective_routes,
            "appliedAt": _utc_now(),
        }

    @staticmethod
    def _current_policy_state(connection: sqlite3.Connection) -> sqlite3.Row | None:
        current = connection.execute(
            "SELECT * FROM thesis_ledger_policy_state WHERE consumer = ?",
            (CONSUMER_NAMESPACE,),
        ).fetchone()
        if current is None:
            return None
        routes = _json_load(current["routes_json"], None)
        effective = _json_load(current["effective_json"], None)
        if (
            not isinstance(routes, list) or not isinstance(effective, dict)
            or effective.get("contractVersion") != CONTROL_CONTRACT_V3_VERSION
        ):
            raise ControlContractError(
                "UNSUPPORTED_STORED_POLICY",
                "数据库中的策略不符合当前合同，请使用开发重建入口处理",
                status_code=409,
            )
        return current

    def _current_v3_effective(self, connection: sqlite3.Connection) -> dict[str, Any] | None:
        current = self._current_policy_state(connection)
        if current is None:
            return None
        policy = {
            "contractVersion": CONTROL_CONTRACT_V3_VERSION,
            "consumer": CONSUMER_NAMESPACE,
            "requestId": str(current["request_id"]),
            "revision": int(current["revision"]),
            "enabled": bool(current["enabled"]),
            "routes": _json_load(current["routes_json"], []),
        }
        return self._effective_v3(connection, policy)

    def apply_policy_v3(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist the current exact route policy under monotonic revision."""
        policy = normalize_policy_v3(payload)
        request_id = policy["requestId"]
        with self._schema_lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                current = self._current_policy_state(connection)
                if current is not None:
                    current_revision = int(current["revision"])
                    current_routes = _json_load(current["routes_json"], [])
                    if policy["revision"] < current_revision:
                        raise ControlContractError(
                            "STALE_REVISION",
                            f"Policy revision {policy['revision']} 早于当前 revision {current_revision}",
                            request_id=request_id,
                            contract_version=CONTROL_CONTRACT_V3_VERSION,
                        )
                    if policy["revision"] == current_revision:
                        if bool(current["enabled"]) != policy["enabled"] or current_routes != policy["routes"]:
                            raise ControlContractError(
                                "REVISION_CONFLICT",
                                "相同 revision 的 Policy 内容不同",
                                request_id=request_id,
                                contract_version=CONTROL_CONTRACT_V3_VERSION,
                            )
                        policy["revision"] = current_revision
                        effective = self._effective_v3(connection, policy)
                        connection.commit()
                        return {
                            "status": "applied",
                            "idempotent": True,
                            "desired": policy,
                            "effective": effective,
                            "requestId": request_id,
                        }

                effective = self._effective_v3(connection, policy)
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
                    contract_version=CONTROL_CONTRACT_V3_VERSION,
                ) from error
            except sqlite3.OperationalError as error:
                connection.rollback()
                if "locked" in str(error).lower():
                    raise ControlContractError(
                        "POLICY_APPLY_CONFLICT",
                        "Policy Apply 正在竞争，请重试",
                        status_code=409,
                        request_id=request_id,
                        contract_version=CONTROL_CONTRACT_V3_VERSION,
                    ) from error
                raise
        return {
            "status": "applied",
            "idempotent": False,
            "desired": policy,
            "effective": effective,
            "requestId": request_id,
        }

    def policy_projection_v3(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            current = self._current_policy_state(connection)
            if current is None:
                return None
            policy = {
                "contractVersion": CONTROL_CONTRACT_V3_VERSION,
                "consumer": CONSUMER_NAMESPACE,
                "requestId": str(current["request_id"]),
                "revision": int(current["revision"]),
                "enabled": bool(current["enabled"]),
                "routes": _json_load(current["routes_json"], []),
            }
            return {
                "status": current["status"],
                "desired": policy,
                "effective": self._effective_v3(connection, policy),
                "lastError": _json_load(current["last_error_json"], None),
                "updatedAt": current["updated_at"],
            }

    def effective_policy_v3(self) -> dict[str, Any] | None:
        projection = self.policy_projection_v3()
        return projection["effective"] if projection else None

    def health(
        self,
        provider_id: str,
        capability: str,
        instrument_type: str,
        *,
        upstream_source: str | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = self._health(
                connection,
                provider_id,
                capability,
                instrument_type,
                upstream_source=upstream_source,
            )
            return dict(row) if row else None

    def claim_provider_request_budget(
        self,
        provider_id: str,
        capability: str,
        instrument_type: str,
        symbol: str,
        *,
        upstream_source: str | None = None,
        request_id: str | None = None,
        now: float | None = None,
        budget_seconds: int = PROVIDER_REQUEST_BUDGET_SECONDS,
    ) -> dict[str, Any]:
        """Atomically reserve one upstream attempt for a durable request key.

        The reservation is written before the caller invokes an adapter.  A
        process crash therefore leaves the same key in cooldown after restart.
        The minimum is deliberately enforced here rather than delegated to
        environment configuration or callers.
        """
        normalized_provider = str(provider_id).strip().lower()
        normalized_capability = str(capability).strip().upper()
        normalized_type = str(instrument_type).strip().upper()
        normalized_symbol = str(symbol).strip().upper()
        normalized_source = str(upstream_source or "").strip().lower()
        if not all((normalized_provider, normalized_capability, normalized_type, normalized_symbol)):
            raise ValueError("Provider request budget key fields must be non-empty")
        current = time.time() if now is None else float(now)
        duration = max(PROVIDER_REQUEST_BUDGET_SECONDS, int(budget_seconds))
        expires_at = current + duration
        request_key = ":".join(
            (CONSUMER_NAMESPACE, normalized_provider, normalized_capability,
             normalized_type, normalized_symbol, normalized_source)
        )
        with self._schema_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT attempted_at, expires_at FROM thesis_ledger_provider_request_budget "
                "WHERE request_key = ?",
                (request_key,),
            ).fetchone()
            if existing is not None and float(existing["expires_at"]) > current:
                connection.rollback()
                return {
                    "allowed": False,
                    "requestKey": request_key,
                    "attemptedAt": float(existing["attempted_at"]),
                    "expiresAt": float(existing["expires_at"]),
                    "remainingSeconds": max(0, int(float(existing["expires_at"]) - current)),
                }
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_request_budget
                (request_key, provider_id, capability, instrument_type, symbol,
                 request_id, attempted_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(request_key) DO UPDATE SET
                  request_id=excluded.request_id,
                  attempted_at=excluded.attempted_at,
                  expires_at=excluded.expires_at
                """,
                (
                    request_key,
                    normalized_provider,
                    normalized_capability,
                    normalized_type,
                    normalized_symbol,
                    request_id,
                    current,
                    expires_at,
                ),
            )
            connection.commit()
        return {
            "allowed": True,
            "requestKey": request_key,
            "attemptedAt": current,
            "expiresAt": expires_at,
            "remainingSeconds": duration,
        }

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
                credential_source, credential_fields, credential_configured, credential_method = _credential_state(
                    manifest, config
                )
                tombstone = connection.execute(
                    "SELECT * FROM thesis_ledger_provider_tombstone WHERE provider_id = ?",
                    (provider_id,),
                ).fetchone()
                if tombstone:
                    configured = False
                elif not manifest.get("requiresCredential", False):
                    configured = True
                else:
                    configured = credential_configured
                result.append(
                    {
                        **manifest,
                        "configured": configured,
                        "enabled": bool(config["enabled"]) if config else True,
                        "credentialConfigured": credential_configured,
                        "credentialSource": credential_source,
                        "credentialFieldsConfigured": credential_fields,
                        "credentialMethod": credential_method,
                        "configVersion": int(config["config_version"]) if config else 0,
                        "updatedAt": config["updated_at"] if config else None,
                        "health": {"scopes": health_by_provider.get(provider_id, [])},
                        "tombstone": (
                            {
                                "providerId": provider_id,
                                "displayName": tombstone["display_name"],
                                "reason": tombstone["reason"],
                                "removedAt": tombstone["removed_at"],
                            }
                            if tombstone
                            else None
                        ),
                    }
                )
            return result

    def provider_credential_snapshot(self, provider_id: str) -> ProviderCredentialSnapshot:
        """Return an immutable runtime snapshot without exposing it in HTTP responses."""

        normalized = _validate_provider_id(provider_id, "runtime-snapshot")
        manifest = PROVIDER_MANIFESTS[normalized]
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM thesis_ledger_provider_config WHERE provider_id = ?",
                (normalized,),
            ).fetchone()
            source, _, configured, method = _credential_state(manifest, row)
            config_version = int(row["config_version"]) if row else 0
            credential_version = int(row["credential_version"]) if row else 0
            values: dict[str, str] = {}
            if source == "control" and row and row["credential_ciphertext"]:
                try:
                    credential = decode_credential_plaintext(
                        _decrypt_secret(
                            str(row["secret_key_version"] or ""),
                            str(row["credential_ciphertext"]),
                        )
                    )
                    validate_stored_credential(normalized, credential)
                    if credential.method == "oauth":
                        validate_oauth_token(
                            credential.values.get("clientId", ""),
                            credential.values.get("tokenJson", ""),
                        )
                except (ControlContractError, ValueError, OAuthStateError) as exc:
                    raise ControlContractError(
                        "SECRET_CREDENTIAL_INVALID",
                        "Provider 页面凭证无法安全解析",
                    ) from exc
                values = dict(credential.values)
            elif source == "environment":
                values = _environment_credential_values(normalized)
                if normalized == "tushare" and values:
                    from data_provider.tushare_endpoint import DEFAULT_TUSHARE_HTTP_URL, _resolve_tushare_http_url

                    values["httpUrl"] = _resolve_tushare_http_url() or DEFAULT_TUSHARE_HTTP_URL
                if normalized == "longbridge" and (
                    values.get("oauthClientId")
                    or not {"appKey", "appSecret", "accessToken"}.issubset(values)
                ):
                    values = {}
                if values:
                    method = {
                        "tushare": "token",
                        "tickflow": "api_key",
                        "finnhub": "api_key",
                        "alphavantage": "api_key",
                        "hithink": "api_key",
                        "longbridge": "legacy",
                    }.get(normalized)
            if not configured and manifest.get("requiresCredential", False):
                values = {}
            return ProviderCredentialSnapshot.create(
                normalized,
                source,
                method,
                values,
                config_version,
                credential_version,
            )

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
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT config_version, credential_version, credential_ciphertext "
                "FROM thesis_ledger_provider_config WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            config_version = (int(existing["config_version"]) if existing else 0) + 1
            credential_version = (int(existing["credential_version"]) if existing else 0) + 1
            connection.execute(
                """
                INSERT INTO thesis_ledger_provider_config
                (provider_id, enabled, settings_json, credential_ciphertext,
                 secret_key_version, config_version, credential_version, updated_at)
                VALUES (?, 0, '{}', NULL, NULL, ?, ?, ?)
                ON CONFLICT(provider_id) DO UPDATE SET
                  enabled=0,
                  credential_ciphertext=NULL,
                  secret_key_version=NULL,
                  config_version=excluded.config_version,
                  credential_version=excluded.credential_version,
                  updated_at=excluded.updated_at
                """,
                (provider_id, config_version, credential_version, now),
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
            effective = self._current_v3_effective(connection)
            connection.commit()
        return {
            "contractVersion": CONTROL_CONTRACT_V3_VERSION,
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
        try:
            validate_config_patch_keys(payload)
        except ValueError as exc:
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                str(exc),
                request_id=request_id,
            ) from exc
        enabled_provided = "enabled" in payload
        enabled_value = payload.get("enabled")
        if enabled_provided and not isinstance(enabled_value, bool):
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                "Provider enabled 必须是布尔值",
                request_id=request_id,
            )
        settings_provided = "settings" in payload
        settings_value = payload.get("settings")
        if settings_provided and not isinstance(settings_value, dict):
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                "Provider settings 必须是对象",
                request_id=request_id,
            )
        credentials_provided = "credentials" in payload
        credential_patch = None
        if credentials_provided:
            try:
                credential_patch = parse_credential_patch(provider_id, payload["credentials"])
            except ValueError as exc:
                raise ControlContractError(
                    "INVALID_PROVIDER_CREDENTIALS",
                    str(exc),
                    request_id=request_id,
                ) from exc
        clear_credentials = payload.get("clearCredentials", False)
        if not isinstance(clear_credentials, bool):
            raise ControlContractError(
                "INVALID_PROVIDER_CONFIG",
                "clearCredentials 必须是布尔值",
                request_id=request_id,
            )
        if clear_credentials and credentials_provided:
            raise ControlContractError(
                "INVALID_PROVIDER_CREDENTIALS",
                "clearCredentials 不能与 credentials 同时提交",
                request_id=request_id,
            )

        with self._schema_lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT * FROM thesis_ledger_provider_config WHERE provider_id = ?",
                    (provider_id,),
                ).fetchone()
                if enabled_provided:
                    enabled = enabled_value
                elif existing is not None:
                    enabled = bool(existing["enabled"])
                else:
                    enabled = True
                if settings_provided:
                    settings = settings_value
                elif existing is not None:
                    settings = _json_load(existing["settings_json"], {})
                else:
                    settings = {}
                existing_encrypted = existing["credential_ciphertext"] if existing else None
                existing_key_version = existing["secret_key_version"] if existing else None
                existing_credential: CredentialValue | None = None
                credential_value_update = credentials_provided
                if existing_encrypted and credential_value_update:
                    try:
                        decoded_credential = decode_credential_plaintext(
                            _decrypt_secret(str(existing_key_version or ""), str(existing_encrypted))
                        )
                        validate_stored_credential(provider_id, decoded_credential)
                        existing_credential = decoded_credential
                    except (ControlContractError, ValueError) as exc:
                        if not clear_credentials:
                            if isinstance(exc, ControlContractError):
                                raise
                            raise ControlContractError(
                                "SECRET_CREDENTIAL_INVALID",
                                "Provider 凭证密文格式无效",
                                request_id=request_id,
                            ) from exc
                encrypted = existing_encrypted
                key_version = existing_key_version
                credential_changed = False
                if clear_credentials:
                    encrypted, key_version = None, None
                    credential_changed = True
                elif credentials_provided and credential_patch is not None:
                    try:
                        merged = merge_credential_patch(
                            provider_id,
                            credential_patch,
                            existing_credential,
                        )
                    except ValueError as exc:
                        raise ControlContractError(
                            "INVALID_PROVIDER_CREDENTIALS",
                            str(exc),
                            request_id=request_id,
                        ) from exc
                    credential_changed = existing_credential != merged
                    if credential_changed:
                        key_version, encrypted = _encrypt_secret(encode_credential_plaintext(merged))
                current_config_version = int(existing["config_version"]) if existing else 0
                current_credential_version = int(existing["credential_version"]) if existing else 0
                config_version = current_config_version + 1
                credential_version = current_credential_version + (1 if credential_changed else 0)
                now = _utc_now()
                connection.execute(
                    """
                    INSERT INTO thesis_ledger_provider_config
                    (provider_id, enabled, settings_json, credential_ciphertext,
                     secret_key_version, config_version, credential_version, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider_id) DO UPDATE SET
                      enabled=excluded.enabled,
                      settings_json=excluded.settings_json,
                      credential_ciphertext=excluded.credential_ciphertext,
                      secret_key_version=excluded.secret_key_version,
                      config_version=excluded.config_version,
                      credential_version=excluded.credential_version,
                      updated_at=excluded.updated_at
                    """,
                    (
                        provider_id,
                        int(enabled),
                        _json(settings),
                        encrypted,
                        key_version,
                        config_version,
                        credential_version,
                        now,
                    ),
                )
                connection.execute(
                    "DELETE FROM thesis_ledger_provider_tombstone WHERE provider_id = ?",
                    (provider_id,),
                )
                effective = self._current_v3_effective(connection)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

            saved_row = connection.execute(
                "SELECT * FROM thesis_ledger_provider_config WHERE provider_id = ?",
                (provider_id,),
            ).fetchone()
            credential_source, credential_fields, credential_configured, credential_method = _credential_state(
                PROVIDER_MANIFESTS[provider_id], saved_row
            )
            return {
                "providerId": provider_id,
                "enabled": enabled,
                "contractVersion": CONTROL_CONTRACT_V3_VERSION,
                "consumer": CONSUMER_NAMESPACE,
                "configured": not PROVIDER_MANIFESTS[provider_id].get("requiresCredential", False) or credential_configured,
                "credentialConfigured": credential_configured,
                "credentialSource": credential_source,
                "credentialFieldsConfigured": credential_fields,
                "credentialMethod": credential_method,
                "configVersion": config_version,
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
        upstream_source: str | None = None,
    ) -> None:
        source = str(upstream_source).strip().lower() if upstream_source else None
        scope_key = ":".join(
            (CONSUMER_NAMESPACE, provider_id, capability, instrument_type, source)
        ) if source else ":".join(
            (CONSUMER_NAMESPACE, provider_id, capability, instrument_type)
        )
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
        in_transaction = connection.in_transaction
        connection.execute(
            """
            INSERT INTO thesis_ledger_catalog_generation
            (generation, checksum, cursor, complete, items_json, created_at)
            VALUES (1, ?, ?, 1, ?, ?)
            """,
            (checksum, cursor, _json(items), _utc_now()),
        )
        if not in_transaction:
            connection.commit()
        return connection.execute(
            "SELECT * FROM thesis_ledger_catalog_generation WHERE generation=1"
        ).fetchone()

    def catalog_snapshot(self, cursor: str | None = None) -> dict[str, Any]:
        from src.services.thesis_ledger_catalog_contract import catalog_snapshot
        return catalog_snapshot(self, cursor)

    def catalog_delta(self, cursor: str) -> dict[str, Any]:
        from src.services.thesis_ledger_catalog_contract import catalog_delta
        return catalog_delta(self, cursor)

    def catalog_ack(self, payload: dict[str, Any]) -> dict[str, Any]:
        from src.services.thesis_ledger_catalog_contract import catalog_ack
        return catalog_ack(self, payload)

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
                if latest is not None:
                    from src.services.thesis_ledger_catalog_contract import current_catalog_row
                    current_catalog_row(latest)
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

    def trigger_catalog_job(self, *, owner: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Create or reuse a Catalog Job without waiting for Provider I/O."""
        if payload is not None:
            from src.services.thesis_ledger_catalog_contract import require_catalog_request
            require_catalog_request(payload)
        job_owner = self._catalog_job_owner_value(owner)
        job, claimed = self._claim_catalog_job(job_owner, initial_status="pending")
        if claimed:
            _catalog_job_manager_for(self.database_path).enqueue(
                str(job["id"]), job_owner
            )
        result = self._job_payload(job)
        return {**result, "requestId": payload["requestId"]} if payload is not None else result

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
            "contractVersion": CONTROL_CONTRACT_V3_VERSION,
            "consumer": CONSUMER_NAMESPACE,
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
