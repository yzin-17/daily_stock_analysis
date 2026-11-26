"""按范围刷新、旧库加法升级、恢复与失败保留。"""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.services import thesis_ledger_catalog_refresh as refresh
from src.services.thesis_ledger_catalog_contract import require_catalog_request
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore

ENVELOPE = {"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "scope"}
HK_US = [{"canonicalCode": "00700", "market": "HK", "instrumentType": "STOCK", "displayName": "腾讯控股"},
         {"canonicalCode": "SPY", "market": "US", "instrumentType": "ETF", "displayName": "标普500ETF"}]


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    result = ThesisLedgerControlStore(str(tmp_path / "catalog.db"))
    result.catalog_snapshot()
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    return result


def run(store, scope):
    row, claimed = store._claim_catalog_job("scope-owner", scope=scope)
    assert claimed
    return store.execute_catalog_job(row["id"], "scope-owner")


def test_refreshes_preserve_other_markets_and_checksum_ack(store, monkeypatch):
    previous = store.catalog_snapshot()["items"]
    read = Mock(return_value=HK_US)
    monkeypatch.setattr(refresh, "_futu_catalog", read)
    job = run(store, "HK_US")
    assert job["status"] == "succeeded" and job["scope"] == "HK_US"
    first = store.catalog_snapshot()
    assert all(item in first["items"] for item in previous + HK_US)
    assert store.catalog_ack({**ENVELOPE, "generation": job["generation"], "checksum": job["checksum"]})["acknowledged"]
    monkeypatch.setattr(refresh.catalog, "build_catalog", Mock(return_value=(previous, {})))
    assert run(store, "CN")["status"] == "succeeded"
    assert store.catalog_snapshot() == first
    read.assert_called_once_with(store)


def test_failure_does_not_publish_or_erase_prior_snapshot(store, monkeypatch):
    before = store.catalog_snapshot()
    monkeypatch.setattr(refresh, "_futu_catalog", Mock(side_effect=refresh.CatalogBuildError("拒绝", retryable=False)))
    assert run(store, "HK_US")["status"] == "failed"
    assert store.catalog_snapshot() == before


def test_wrong_market_does_not_publish(store, monkeypatch):
    before = store.catalog_snapshot()
    monkeypatch.setattr(refresh, "_futu_catalog", Mock(return_value=before["items"]))
    assert run(store, "HK_US")["status"] == "failed"
    assert store.catalog_snapshot() == before


def test_pending_scope_survives_restart_and_other_scope_is_busy(store):
    row, _ = store._claim_catalog_job("original", scope="HK_US")
    restarted = ThesisLedgerControlStore(store.database_path)
    assert restarted.get_catalog_job(row["id"])["scope"] == "HK_US"
    duplicate, claimed = restarted._claim_catalog_job("other", scope="HK_US")
    assert not claimed and duplicate["id"] == row["id"]
    with pytest.raises(ControlContractError) as error:
        restarted._claim_catalog_job("cn", scope="CN")
    assert error.value.code == "CATALOG_SCOPE_BUSY"


def test_trigger_persists_requested_scope_before_enqueue(store, monkeypatch):
    worker = Mock()
    monkeypatch.setattr("src.services.thesis_ledger_control._catalog_job_manager_for", Mock(return_value=worker))
    job = store.trigger_catalog_job(payload={**ENVELOPE, "scope": "HK_US"})
    assert job["scope"] == "HK_US" and job["requestId"] == "scope"
    worker.enqueue.assert_called_once_with(job["id"], job["owner"])
    assert ThesisLedgerControlStore(store.database_path).get_catalog_job(job["id"])["scope"] == "HK_US"


def test_expired_hk_us_is_not_automatically_retried(store):
    row, _ = store._claim_catalog_job("scope-owner", scope="HK_US", initial_status="running")
    with store._connect() as connection:
        connection.execute("UPDATE thesis_ledger_catalog_job SET lease_expires_at='2000-01-01T00:00:00+00:00'")
    assert store.recover_catalog_jobs("restart") == []
    assert store.get_catalog_job(row["id"])["status"] == "timeout"


@pytest.mark.parametrize("scope", ["US", "all", [], None])
def test_invalid_request_scopes_rejected(scope):
    with pytest.raises(ControlContractError):
        require_catalog_request({**ENVELOPE, "scope": scope})


def test_ack_cannot_include_refresh_scope():
    with pytest.raises(ControlContractError):
        require_catalog_request({**ENVELOPE, "scope": "HK_US", "generation": 1, "checksum": "a" * 64}, ack=True)


def test_futu_uses_existing_enabled_credentials_with_one_hard_deadline(monkeypatch):
    store = SimpleNamespace(provider_registry=Mock(return_value=[{
        "providerId": "futu", "enabled": True, "configured": True}]),
        provider_credential_snapshot=Mock(return_value=SimpleNamespace(values={"host": "localhost", "port": "11111"})))
    bounded = Mock(return_value=HK_US)
    monkeypatch.setattr(refresh.catalog, "_bounded_call", bounded)
    assert refresh._futu_catalog(store) == HK_US
    _, kwargs = bounded.call_args
    assert kwargs == {"timeout_seconds": 60, "max_attempts": 1}
    store.provider_credential_snapshot.assert_called_once_with("futu")
    store.provider_registry.return_value[0]["enabled"] = False
    with pytest.raises(refresh.CatalogBuildError):
        refresh._futu_catalog(store)
    assert bounded.call_count == 1


def test_legacy_schema_scope_upgrade_is_additive_and_idempotent(tmp_path):
    path = str(tmp_path / "legacy.db")
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE thesis_ledger_catalog_job (id TEXT PRIMARY KEY, status TEXT NOT NULL, "
                           "generation INTEGER NOT NULL, checksum TEXT NOT NULL, error_json TEXT, created_at TEXT NOT NULL)")
        connection.execute("INSERT INTO thesis_ledger_catalog_job VALUES ('old','failed',1,'',NULL,'2026-01-01')")
    ThesisLedgerControlStore(path)
    ThesisLedgerControlStore(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT id,scope FROM thesis_ledger_catalog_job").fetchall() == [("old", "CN")]
