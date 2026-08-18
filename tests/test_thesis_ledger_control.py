"""Control Contract V1 的原子性、权限和目录 fixture 回归。"""

import sqlite3
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore


def _client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stock_analysis.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "0123456789abcdef-secret-key")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def _envelope(**values):
    return {
        "contractVersion": 1,
        "consumer": "thesis-ledger",
        "requestId": "control-test-request",
        **values,
    }


class _NoopLock:
    """模拟独立 worker，避免进程内锁掩盖 SQLite 跨连接竞态。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class _CoordinatedControlStore(ThesisLedgerControlStore):
    """在读取空状态后暂停，以稳定复现旧实现的跨连接竞态。"""

    _schema_lock = _NoopLock()

    def __init__(self, database_path, role, state_read, started, release):
        self._role = role
        self._state_read = state_read
        self._started = started
        self._release = release
        super().__init__(database_path)

    def apply_policy(self, payload):
        self._started.set()
        return super().apply_policy(payload)

    def _current_state(self, connection):
        current = super()._current_state(connection)
        if current is None:
            self._state_read.set()
            if not self._release.wait(timeout=5):
                raise RuntimeError(f"{self._role} test worker timed out")
        return current


def _run_cross_connection_apply_race(tmp_path, lower_payload, upper_payload):
    database_path = str(tmp_path / "policy-race.db")
    lower_read = threading.Event()
    upper_read = threading.Event()
    lower_started = threading.Event()
    upper_started = threading.Event()
    lower_release = threading.Event()
    upper_release = threading.Event()
    lower_done = threading.Event()
    upper_done = threading.Event()
    lower = _CoordinatedControlStore(
        database_path, "lower", lower_read, lower_started, lower_release
    )
    upper = _CoordinatedControlStore(
        database_path, "upper", upper_read, upper_started, upper_release
    )
    results = {}

    def invoke(role, store, payload, done):
        try:
            results[role] = store.apply_policy(payload)
        except Exception as error:  # noqa: BLE001 - assertions inspect the contract error.
            results[role] = error
        finally:
            done.set()

    lower_thread = threading.Thread(
        target=invoke, args=("lower", lower, lower_payload, lower_done)
    )
    upper_thread = threading.Thread(
        target=invoke, args=("upper", upper, upper_payload, upper_done)
    )
    try:
        lower_thread.start()
        assert lower_started.wait(timeout=2)
        assert lower_read.wait(timeout=2)
        upper_thread.start()
        assert upper_started.wait(timeout=2)

        # 旧实现会在 BEGIN IMMEDIATE 之前读到空状态，此时先让 upper
        # 提交，再放行 lower；修复后的实现会让 upper 阻塞在写锁上。
        if upper_read.wait(timeout=2):
            upper_release.set()
            assert upper_done.wait(timeout=5)
            lower_release.set()
        else:
            lower_release.set()
            assert lower_done.wait(timeout=5)
            upper_release.set()
        assert lower_done.wait(timeout=5)
        assert upper_done.wait(timeout=5)
    finally:
        lower_release.set()
        upper_release.set()
        lower_thread.join(timeout=5)
        upper_thread.join(timeout=5)

    assert not lower_thread.is_alive()
    assert not upper_thread.is_alive()
    projection = ThesisLedgerControlStore(database_path).policy_projection()
    return results, projection


def test_control_token_is_independent_and_handshake_is_versioned(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.post(
        "/api/v1/thesis-ledger/control/handshake",
        headers={"authorization": "Bearer data-token"},
        json=_envelope(supportedVersions=[1]),
    )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"

    response = client.post(
        "/api/v1/thesis-ledger/control/handshake",
        headers={"authorization": "Bearer control-token"},
        json=_envelope(supportedVersions=[1]),
    )
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert response.json()["consumer"] == "thesis-ledger"


def test_data_routes_reject_missing_or_control_token(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    for headers in ({}, {"authorization": "Bearer control-token"}):
        response = client.get(
            "/api/v1/thesis-ledger/market/quote?symbol=600519.SH",
            headers=headers,
        )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "unauthorized"


def test_fund_nav_history_is_ordered_and_uses_of_identity(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    response = client.get(
        "/api/v1/thesis-ledger/market/fund-nav/history?symbol=000001&limit=5",
        headers={"authorization": "Bearer data-token"},
    )
    assert response.status_code == 200
    points = response.json()
    assert len(points) == 5
    assert {point["symbol"] for point in points} == {"000001.OF"}
    assert [point["navDate"] for point in points] == sorted(
        point["navDate"] for point in points
    )


def test_policy_apply_is_latest_wins_idempotent_and_atomic(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    config = client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/config",
        headers=headers,
        json=_envelope(credential="fixture-secret", enabled=True, settings={}),
    )
    assert config.status_code == 200
    assert config.json()["credentialConfigured"] is True
    assert "fixture-secret" not in config.text

    policy = _envelope(
        revision=1,
        enabled=True,
        routes={"REALTIME_QUOTE": {"STOCK": ["akshare", "efinance"]}},
    )
    applied = client.post(
        "/api/v1/thesis-ledger/control/policies/apply", headers=headers, json=policy
    )
    assert applied.status_code == 200
    assert applied.json()["effective"]["sourceDesiredRevision"] == 1
    assert applied.json()["effective"]["routeStatus"]["REALTIME_QUOTE"]["STOCK"][
        "eligibleProviderIds"
    ] == ["akshare", "efinance"]

    repeated = client.post(
        "/api/v1/thesis-ledger/control/policies/apply", headers=headers, json=policy
    )
    assert repeated.status_code == 200
    assert repeated.json()["idempotent"] is True

    conflict = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json={**policy, "enabled": False},
    )
    assert conflict.status_code == 422
    assert conflict.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert "UNIQUE" not in conflict.text.upper()

    stale = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json={**policy, "revision": 0},
    )
    assert stale.status_code == 422
    assert stale.json()["detail"]["code"] == "INVALID_REVISION"

    invalid = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json={
            **policy,
            "revision": 2,
            "routes": {"REALTIME_QUOTE": {"STOCK": ["tushare"]}},
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "UNKNOWN_PROVIDER"
    effective = client.get(
        "/api/v1/thesis-ledger/control/policies/effective", headers=headers
    )
    assert effective.json()["projection"]["desired"]["revision"] == 1


def test_policy_apply_serializes_cross_connection_revision_writes(tmp_path):
    results, projection = _run_cross_connection_apply_race(
        tmp_path,
        _envelope(revision=1, enabled=True, routes={}),
        _envelope(revision=2, enabled=True, routes={}),
    )

    assert not isinstance(results["lower"], Exception)
    assert not isinstance(results["upper"], Exception)
    assert projection is not None
    assert projection["desired"]["revision"] == 2
    assert projection["effective"]["sourceDesiredRevision"] == 2


def test_concurrent_same_revision_conflict_is_stable_control_error(tmp_path):
    results, projection = _run_cross_connection_apply_race(
        tmp_path,
        _envelope(revision=1, enabled=True, routes={}),
        _envelope(revision=1, enabled=False, routes={}),
    )

    successful = [value for value in results.values() if not isinstance(value, Exception)]
    conflicts = [
        value
        for value in results.values()
        if isinstance(value, ControlContractError) and value.code == "REVISION_CONFLICT"
    ]
    assert len(successful) == 1
    assert len(conflicts) == 1
    assert all(not isinstance(value, sqlite3.IntegrityError) for value in results.values())
    assert projection is not None
    assert projection["desired"]["revision"] == 1


def test_chip_summary_manifest_route_is_explicit_and_unsupported_provider_is_atomic(
    monkeypatch, tmp_path
):
    """CHIP_SUMMARY 只接受 manifest 声明的 Provider，非法 route 不改变旧策略。"""
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    registry = client.get("/api/v1/thesis-ledger/control/providers", headers=headers)
    assert registry.status_code == 200
    providers = {item["providerId"]: item for item in registry.json()["providers"]}
    assert providers["akshare"]["capabilities"]["CHIP_SUMMARY"] == ["STOCK"]
    assert "CHIP_SUMMARY" not in providers["efinance"]["capabilities"]

    valid = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json=_envelope(
            revision=1,
            enabled=True,
            routes={"CHIP_SUMMARY": {"STOCK": ["akshare"]}},
        ),
    )
    assert valid.status_code == 200
    assert valid.json()["effective"]["routeStatus"]["CHIP_SUMMARY"]["STOCK"][
        "eligibleProviderIds"
    ] == ["akshare"]

    invalid = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json=_envelope(
            revision=2,
            enabled=True,
            routes={"CHIP_SUMMARY": {"STOCK": ["efinance"]}},
        ),
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "UNSUPPORTED_ROUTE"
    unknown = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json=_envelope(
            revision=2,
            enabled=True,
            routes={"CHIP_DISTRIBUTION": {"STOCK": ["akshare"]}},
        ),
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["code"] == "UNSUPPORTED_CAPABILITY"
    effective = client.get(
        "/api/v1/thesis-ledger/control/policies/effective", headers=headers
    )
    assert effective.json()["projection"]["desired"]["revision"] == 1


def test_empty_routes_are_valid_and_disable_all_effective_routes(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    response = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=headers,
        json=_envelope(revision=1, enabled=True, routes={}),
    )
    assert response.status_code == 200
    assert response.json()["effective"]["routes"] == {}


def test_catalog_snapshot_delta_and_expired_cursor(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer data-token"}
    snapshot = client.get("/api/v1/thesis-ledger/catalog/snapshot", headers=headers)
    assert snapshot.status_code == 200
    payload = snapshot.json()
    assert payload["complete"] is True
    assert payload["generation"] == 1
    assert payload["checksum"]

    delta = client.get(
        f"/api/v1/thesis-ledger/catalog/delta?cursor={payload['cursor']}", headers=headers
    )
    assert delta.status_code == 200
    assert delta.json()["items"] == []

    expired = client.get(
        "/api/v1/thesis-ledger/catalog/delta?cursor=generation%3A999", headers=headers
    )
    assert expired.status_code == 409
    assert expired.json()["detail"]["code"] == "CATALOG_CURSOR_EXPIRED"


def test_capability_smoke_does_not_change_policy(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    response = client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/test",
        headers=headers,
        json=_envelope(credential="ephemeral-only", capabilities=["REALTIME_QUOTE"]),
    )
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["capabilityResults"]["REALTIME_QUOTE"]["attempted"] is True
    assert response.json()["capabilityResults"]["FUND_NAV_HISTORY"]["status"] == "healthy"
    assert response.json()["capabilityResults"]["FUND_NAV_HISTORY"]["attempted"] is True
    assert response.json()["capabilityResults"]["CHIP_SUMMARY"]["status"] == "healthy"
    assert response.json()["capabilityResults"]["CHIP_SUMMARY"]["attempted"] is True
    registry = client.get("/api/v1/thesis-ledger/control/providers", headers=headers)
    assert registry.json()["providers"][0]["credentialConfigured"] is False


def test_provider_removal_clears_runtime_config_but_keeps_tombstone(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    headers = {"authorization": "Bearer control-token"}
    client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/config",
        headers=headers,
        json=_envelope(credential="fixture-secret", enabled=True),
    )
    response = client.post(
        "/api/v1/thesis-ledger/control/providers/akshare/remove",
        headers=headers,
        json=_envelope(reason="test-removal"),
    )
    assert response.status_code == 200
    assert response.json()["tombstone"]["providerId"] == "akshare"
    registry = client.get("/api/v1/thesis-ledger/control/providers", headers=headers).json()
    removed = next(item for item in registry["providers"] if item["providerId"] == "akshare")
    assert removed["credentialConfigured"] is False
    assert removed["tombstone"]["reason"] == "test-removal"
