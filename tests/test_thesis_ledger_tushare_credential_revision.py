"""Tushare 环境凭据与服务地址的准入指纹及执行快照。"""

import json as json_module
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


def snapshot(token="fixture-token", endpoint="https://fixture.example/api", version=0):
    return ProviderCredentialSnapshot.create(
        "tushare", "environment", "token", {"token": token, "httpUrl": endpoint}, version, version,
    )


@pytest.fixture(autouse=True)
def master(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "fixture-master")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "fixture-v1")
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)


def test_revision_binds_token_endpoint_and_master_but_not_unrelated_store_versions(monkeypatch):
    original = _provider_credential_revision_from_snapshot(snapshot())
    assert original.startswith("hmac-sha256-v1:")
    assert "fixture" not in original
    assert original == _provider_credential_revision_from_snapshot(snapshot(version=99))
    assert original != _provider_credential_revision_from_snapshot(snapshot(token="rotated-token"))
    assert original != _provider_credential_revision_from_snapshot(snapshot(endpoint="https://other.example/api"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "rotated-master")
    assert original != _provider_credential_revision_from_snapshot(snapshot())
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "fixture-master")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "fixture-v2")
    assert original != _provider_credential_revision_from_snapshot(snapshot())


@pytest.mark.parametrize("values", [
    {}, {"token": "fixture-token"}, {"httpUrl": "https://fixture.example/api"},
    {"token": "", "httpUrl": "https://fixture.example/api"},
    {"token": "fixture-token", "httpUrl": "invalid"},
])
def test_incomplete_snapshot_cannot_obtain_a_revision(values):
    incomplete = ProviderCredentialSnapshot.create("tushare", "environment", "token", values, 0, 0)
    assert _provider_credential_revision_from_snapshot(incomplete) is None


def test_missing_master_fails_closed(monkeypatch):
    monkeypatch.delenv("THESIS_LEDGER_DSA_SECRET_KEY")
    assert _provider_credential_revision_from_snapshot(snapshot()) is None


def test_store_snapshot_freezes_default_endpoint_and_redacts_registry(monkeypatch, tmp_path):
    monkeypatch.delenv("TUSHARE_HTTP_URL", raising=False)
    with patch("src.config.get_config", return_value=SimpleNamespace(tushare_token="fixture-token")):
        store = ThesisLedgerControlStore(str(tmp_path / "tushare.sqlite"))
        frozen = store.provider_credential_snapshot("tushare")
        public = json_module.dumps(store.provider_registry())
    assert frozen.values["httpUrl"] == "http://api.tushare.pro"
    assert _provider_credential_revision_from_snapshot(frozen) is not None
    assert "fixture-token" not in repr(frozen) + public
    assert "httpUrl" not in public and "credentialRevision" not in public


def test_exact_http_partitions_use_frozen_endpoint_even_if_environment_changes(monkeypatch, tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "tushare.sqlite"))
    frozen = snapshot()
    runtime = ThesisLedgerProviderRuntime(store)
    adapter = runtime._adapter("tushare", snapshot=frozen, cache=False)
    monkeypatch.setenv("TUSHARE_HTTP_URL", "https://later.example/api")
    urls = []

    def response(url, *, json, timeout, allow_redirects):
        urls.append(url)
        assert allow_redirects is False
        assert json["token"] == "fixture-token"
        assert json["api_name"] == "fund_daily"
        return Mock(status_code=200, text=json_module.dumps({
            "code": 0, "data": {
                "fields": ["ts_code", "trade_date", "open", "high", "low", "close", "vol", "amount"],
                "items": [["159516.SZ", json["params"]["start_date"], 1, 2, 1, 2, 10, 1]],
            },
        }))

    with patch("data_provider.tushare_fetcher.requests.post", side_effect=response):
        result = adapter.get_daily_data_for_source(
            "159516.SZ", "tushare", start_date="2024-01-01", end_date="2025-01-02", adjustment="none",
        )
    assert len(result) == 2
    assert urls == ["https://fixture.example/api", "https://fixture.example/api"]
    assert _provider_credential_revision_from_snapshot(frozen) == _provider_credential_revision_from_snapshot(snapshot())


def test_adapter_cache_keeps_rate_counter_but_invalidates_on_same_version_credential_rotation(tmp_path):
    runtime = ThesisLedgerProviderRuntime(ThesisLedgerControlStore(str(tmp_path / "cache.sqlite")))
    initial = runtime._adapter("tushare", snapshot=snapshot())
    initial._call_count = 17
    assert runtime._adapter("tushare", snapshot=snapshot()) is initial
    assert runtime._adapter("tushare", snapshot=snapshot())._call_count == 17
    rotated = runtime._adapter("tushare", snapshot=snapshot(token="rotated-token"))
    assert rotated is not initial
    relocated = runtime._adapter("tushare", snapshot=snapshot(token="rotated-token", endpoint="https://other.example/api"))
    assert relocated is not rotated
    assert relocated._api._api_url == "https://other.example/api"
