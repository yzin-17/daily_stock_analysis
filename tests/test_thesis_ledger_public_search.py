"""网页搜索走真实控制、固定来源和本机受控 HTTP，不使用外部搜索配额。"""
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from api.thesis_ledger_public_search import router
from src import data_config
from src.data_config import DataServiceConfig
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from src.services.thesis_ledger_public_search import SearchRequest, execute_public_search
from tests import test_thesis_ledger_news_execution as execution_tests
server = execution_tests.server
KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "WEB_SEARCH"}
TARGET = {"providerId": "searxng", "upstreamSource": "searxng-news-search"}


@pytest.fixture
def environment(server, monkeypatch, tmp_path):
    settings, state = server
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "controlled-search-store-key")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "controlled-search-token")
    store = ThesisLedgerControlStore(str(tmp_path / "search.db"))
    store.save_provider_config("searxng", {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-enable",
        "enabled": True, "credentials": {"method": "search", "values": {"baseUrls": json.dumps(settings.base_urls)}}})
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-policy", "revision": 1,
        "enabled": True, "routes": [{"key": KEY, "targets": [TARGET], "enabled": True}]})
    runtime = ThesisLedgerProviderRuntime(store)
    from api import thesis_ledger_public_search as endpoint
    monkeypatch.setattr(endpoint, "get_thesis_ledger_runtime", lambda: runtime)
    app = FastAPI(); app.include_router(router, prefix="/api/v3")
    return TestClient(app), store, runtime, state


def query():
    return SearchRequest(market="CN", query="半导体产业链 公开资料", limit=5)


def test_public_query_uses_selected_source_and_preserves_original_content(environment):
    client, _, _, state = environment
    result = client.post("/api/v3/thesis-ledger/research/search", json=query().model_dump(),
                         headers={"authorization": "Bearer controlled-search-token"})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "ready" and body["provider"] == "searxng"
    assert body["query"] == query().query and body["coverage"] == "last_year"
    assert body["items"][0]["snippet"] == "原文片段"
    assert body["items"][0]["publishedAt"] == "2026-10-18T09:00:00+08:00"
    assert state["calls"][0]["q"] == [query().query]
    assert "__PUBLIC_SEARCH__" not in str(state["calls"])


def test_missing_auth_and_bad_input_never_call_source(environment):
    client, _, _, state = environment
    assert client.post("/api/v3/thesis-ledger/research/search", json=query().model_dump()).status_code in {401, 403}
    bad = {**query().model_dump(), "limit": 11}
    assert client.post("/api/v3/thesis-ledger/research/search", json=bad,
                       headers={"authorization": "Bearer controlled-search-token"}).status_code == 422
    assert state["calls"] == []


def test_disabled_source_and_other_market_do_not_use_existing_news_or_environment_routes(environment):
    _, store, runtime, state = environment
    result = execute_public_search(runtime, query().model_copy(update={"market": "US"}))
    assert result.status == "unavailable" and result.reason == "not_admitted"
    store.save_provider_config("searxng", {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "disable", "enabled": False})
    assert execute_public_search(runtime, query()).status == "unavailable"
    assert state["calls"] == []


@pytest.mark.parametrize("mode,reason", [("empty", None), ("rate", "rate_limited"), ("malformed", "upstream_failure")])
def test_empty_success_is_distinct_from_source_failures(environment, mode, reason):
    _, _, runtime, state = environment; state["mode"] = mode
    result = execute_public_search(runtime, query())
    assert result.status == ("unavailable" if reason else "ready")
    assert result.reason == reason
    assert result.upstreamAttempts == 1
    assert len(state["calls"]) == 1


def test_control_change_after_actual_source_read_discards_late_result(environment, monkeypatch):
    _, store, runtime, state = environment
    from src.services import thesis_ledger_public_search as service
    original = runtime._execute_request_with_boundary
    def late(*args):
        result = original(*args)
        store.save_provider_config("searxng", {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "late-disable", "enabled": False})
        return result
    monkeypatch.setattr(runtime, "_execute_request_with_boundary", late)
    result = service.execute_public_search(runtime, query())
    assert result.status == "unavailable" and result.reason == "not_admitted"
    assert result.upstreamAttempts == 1
    assert len(state["calls"]) == 1
