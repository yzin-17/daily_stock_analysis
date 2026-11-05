"""新闻来源的默认关闭、精确准入与只写凭据声明。"""

from dataclasses import replace
import json

import pytest

from data_provider.thesis_ledger_sources.news import SOURCES
from src import data_config
from src.data_config import DataServiceConfig
from src.services.provider_credentials import parse_credential_patch, credential_schema
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore
from src.services.thesis_ledger_news_service import NEWS_UPSTREAMS


KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "NEWS_SEARCH"}
TARGET = {"providerId": "searxng", "upstreamSource": NEWS_UPSTREAMS["searxng"]}


@pytest.fixture
def store(monkeypatch, tmp_path):
    config = DataServiceConfig(
        tavily_api_keys=["controlled-private-news-value"], serpapi_keys=["controlled-private-news-value"],
        bocha_api_keys=["controlled-private-news-value"], anspire_api_keys=["controlled-private-news-value"],
        minimax_api_keys=["controlled-private-news-value"], brave_api_keys=["controlled-private-news-value"],
        searxng_base_urls=["https://controlled-search.example"],
    )
    monkeypatch.setattr(data_config, "get_data_config", lambda: config)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "controlled-news-store-secret-key")
    return ThesisLedgerControlStore(str(tmp_path / "control.db"))


def policy(*, enabled=True):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "news-control",
            "revision": 1, "enabled": True,
            "routes": [{"key": KEY, "targets": [TARGET], "enabled": enabled}]}


def test_environment_credentials_do_not_enable_new_news_sources(store):
    rows = {item["providerId"]: item for item in store.provider_registry()}
    for source in SOURCES:
        row = rows[source.provider_id]
        assert row["configured"] and row["credentialConfigured"]
        assert row["enabled"] is False and row["configVersion"] == 0
        assert source.default_enabled is False
        assert all(binding.admission_required is True for binding in source.bindings)
    assert rows["tencent"]["enabled"] is True
    assert "controlled-private-news-value" not in json.dumps(list(rows.values()))
    with store._connect() as connection:
        assert connection.execute("SELECT count(*) FROM thesis_ledger_provider_config").fetchone()[0] == 0


def test_news_route_requires_explicit_enable_and_keeps_independent_admission(store):
    missing_flag = policy()
    del missing_flag["routes"][0]["enabled"]
    with pytest.raises(ControlContractError) as failure:
        store.apply_policy_v3(missing_flag)
    assert failure.value.code == "INVALID_POLICY_SCHEMA"
    store.apply_policy_v3(policy())
    applied = store.effective_policy_v3()
    assert applied["routes"][0]["targets"][0]["eligible"] is False
    assert applied["routes"][0]["targets"][0]["reason"] == "disabled"
    payload = {"contractVersion": 3, "consumer": "thesis-ledger",
               "requestId": "explicit-local-enable", "enabled": True}
    store.save_provider_config("searxng", payload)
    effective = store.effective_policy_v3()
    target = effective["routes"][0]["targets"][0]
    assert target["eligible"] is False and target["reason"] == "not_admitted"
    assert store.get_route_admission_v3(key=KEY, target=TARGET) is None


def test_first_settings_write_preserves_news_default_closed_and_legacy_defaults(store):
    payload = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "settings-only", "settings": {}}
    store.save_provider_config("tavily", payload)
    store.save_provider_config("tencent", payload)
    rows = {item["providerId"]: item for item in store.provider_registry()}
    assert rows["tavily"]["configured"] and rows["tavily"]["enabled"] is False
    assert rows["tencent"]["enabled"] is True


@pytest.mark.parametrize("source", SOURCES, ids=lambda source: source.provider_id)
def test_exact_bindings_and_structured_secret_schema(source):
    field = "baseUrls" if source.provider_id == "searxng" else "apiKeys"
    value = "https://search.example" if field == "baseUrls" else "controlled-private-key"
    patch = parse_credential_patch(source.provider_id, {"method": "search", "values": {field: json.dumps([value])}})
    assert json.loads(patch.values[field]) == [value]
    assert credential_schema(source.provider_id)["methods"][0]["method"] == "search"
    with pytest.raises(ValueError, match="有效的非空"):
        parse_credential_patch(source.provider_id, {"method": "search", "values": {field: "[]"}})
    for binding in source.bindings:
        assert CAPABILITY_SOURCES.resolve(binding.key.route_key(), {
            "providerId": source.provider_id, "upstreamSource": binding.upstream_source,
        }) is binding
        assert binding.key.capability == "NEWS_SEARCH" and binding.gated
    assert CAPABILITY_SOURCES.resolve(KEY, {"providerId": source.provider_id, "upstreamSource": "other"}) is None


def test_default_enable_declaration_must_be_boolean():
    with pytest.raises(ValueError, match="默认启停"):
        CapabilityRegistry((replace(SOURCES[0], default_enabled="false"),))
