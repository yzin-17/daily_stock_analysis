"""正式选路、准入、HTTP 合同与缓存撤销的受控验证。"""

from datetime import datetime, timedelta, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api.thesis_ledger_news import build_news_router
from src import data_config
from src.data_config import DataServiceConfig
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_news_contract_v1 import NewsRequest, NewsResponse
from src.services.thesis_ledger_news_gateway import execute_news_request
from src.services.thesis_ledger_news_service import NEWS_UPSTREAMS
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from data_provider.thesis_ledger_sources import news as news_source
from tests import test_thesis_ledger_news_execution as execution_tests

server = execution_tests.server
KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "NEWS_SEARCH"}
TARGET = {"providerId": "searxng", "upstreamSource": NEWS_UPSTREAMS["searxng"]}
HEADERS = {"authorization": "Bearer controlled-news-data-token"}


def payload(**overrides):
    return {"requestId": "news-formal-request", "instrument": {
        "symbol": "000001.SZ", "market": "CN", "assetType": "STOCK"}, **overrides}


def enable(store, provider, values):
    return store.save_provider_config(provider, {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-enable", "enabled": True,
        "credentials": {"method": "search", "values": values},
    })


def admit(store, target=TARGET):
    now = datetime.now(timezone.utc)
    return store.record_route_admission_v3(
        key=KEY, target=target, evidence_ref="controlled-http-test-only", evidence_sha256="a" * 64,
        scope_symbols=["000001.SZ"], scope_date_from=(now - timedelta(days=400)).date().isoformat(),
        scope_date_to=(now + timedelta(days=1)).date().isoformat(),
        adapter_revision=news_source.ADAPTER_REVISION, source_revision=news_source.SOURCE_REVISION,
        credential_revision=_provider_credential_revision_from_snapshot(store.provider_credential_snapshot(target["providerId"])),
        valid_from=(now - timedelta(minutes=1)).isoformat(), valid_until=(now + timedelta(hours=1)).isoformat(),
        recorded_by="controlled-test-only",
    )


def apply(store, *, revision=1, targets=None):
    return store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-policy",
                                  "revision": revision, "enabled": True,
                                  "routes": [{"key": KEY, "targets": targets or [TARGET], "enabled": True}]})


@pytest.fixture
def environment(server, monkeypatch, tmp_path):
    settings, state = server
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "controlled-news-formal-store-key")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "controlled-news-data-token")
    store = ThesisLedgerControlStore(str(tmp_path / "control.db"))
    enable(store, "searxng", {"baseUrls": json.dumps(settings.base_urls)})
    admit(store)
    apply(store)
    runtime = ThesisLedgerProviderRuntime(store)
    app = FastAPI()
    app.include_router(build_news_router(lambda: runtime), prefix="/api/v3/thesis-ledger")
    return TestClient(app), store, runtime, state


def read(client, body=None, **kwargs):
    response = client.post("/api/v3/thesis-ledger/market/news", json=body or payload(), headers=HEADERS, **kwargs)
    assert response.status_code == 200, response.text[:500]
    return NewsResponse.model_validate(response.json())


def test_formal_binding_http_and_same_request_replay_preserve_original_facts(environment):
    client, _, _, state = environment
    first = read(client)
    second = read(client)
    assert first.status == "available" and first == second, first.errors
    assert first.upstreamAttempts == len(state["calls"]) == 1
    assert first.route.providerId == "searxng" and first.route.policyRevision == "effective:1"
    assert "hmac" not in first.model_dump_json() and "controlled-news-formal-store-key" not in first.model_dump_json()


@pytest.mark.parametrize("mode,status,code", [
    ("empty", "available", None), ("partial", "partial", "upstream_failure"),
    ("malformed", "unavailable", "upstream_failure"), ("rate", "unavailable", "rate_limited"),
])
def test_formal_response_preserves_empty_partial_failure_and_limits(environment, mode, status, code):
    client, _, _, state = environment
    state["mode"] = mode
    result = read(client)
    assert result.status == status and result.upstreamAttempts == len(state["calls"]) == 1
    assert code is None or code in {error.code for error in result.errors}
    if mode in {"empty", "malformed", "rate"}:
        assert result.items == []


@pytest.mark.parametrize("action", ["revoke", "disable"])
def test_revocation_and_disable_reject_before_replaying_cached_response(environment, action):
    client, store, _, state = environment
    assert read(client).status == "available"
    if action == "revoke":
        store.revoke_route_admission_v3(key=KEY, target=TARGET, reason="controlled-revoke")
    else:
        store.save_provider_config("searxng", {
            "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-disable", "enabled": False,
        })
    result = read(client)
    assert result.status == "unavailable" and result.upstreamAttempts == 0
    assert {error.code for error in result.errors} == {"not_admitted"}
    assert len(state["calls"]) == 1


def test_config_and_policy_revisions_separate_cached_identity(environment):
    client, store, _, state = environment
    first = read(client)
    store.save_provider_config("searxng", {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-settings-revision", "settings": {},
    })
    assert read(client).upstreamAttempts == 0
    admit(store)
    second = read(client)
    assert second.status == "available" and len(state["calls"]) == 2
    assert first.route.credentialRevision != second.route.credentialRevision
    apply(store, revision=2)
    third = read(client)
    assert third.route.policyRevision == "effective:2" and len(state["calls"]) == 3


def test_new_request_and_changed_window_or_limit_do_not_replay_another_business_request(environment):
    client, _, _, state = environment
    assert read(client).status == "available"
    for changes in [{"requestId": "another-request"}, {"limit": 2}, {"publishedStart": "2026-10-18T00:00:00Z"}]:
        assert read(client, payload(**changes)).status == "available"
    assert len(state["calls"]) == 4


def test_control_change_after_actual_read_discards_result_without_falsifying_zero_attempts(environment, monkeypatch):
    client, store, _, state = environment
    original = news_source.search_news

    def changed(*args, **kwargs):
        response = original(*args, **kwargs)
        store.save_provider_config("searxng", {
            "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-inflight-disable", "enabled": False,
        })
        return response

    monkeypatch.setattr(news_source, "search_news", changed)
    result = read(client)
    assert result.status == "unavailable" and result.items == []
    assert result.upstreamAttempts == len(state["calls"]) == 1
    assert {error.code for error in result.errors} == {"upstream_failure"}


def test_http_auth_and_private_or_invalid_request_rejected_without_search(environment):
    client, _, _, state = environment
    assert client.post("/api/v3/thesis-ledger/market/news", json=payload()).status_code == 401
    response = client.post("/api/v3/thesis-ledger/market/news", headers=HEADERS,
                           json=payload(privateHolding="controlled-private-material"))
    assert response.status_code == 422 and "controlled-private-material" not in response.text
    result = read(client, payload(instrument={"symbol": "000002.SZ", "market": "CN", "assetType": "STOCK"}))
    assert result.status == "unavailable" and result.upstreamAttempts == 0
    assert {error.code for error in result.errors} == {"not_admitted"}
    assert state["calls"] == []


def test_absent_policy_has_zero_attempts_and_does_not_create_default_news_route(environment, tmp_path):
    _, _, _, state = environment
    store = ThesisLedgerControlStore(str(tmp_path / "no-policy.db"))
    result = execute_news_request(ThesisLedgerProviderRuntime(store), NewsRequest.model_validate(payload()))
    assert result.status == "unavailable" and result.upstreamAttempts == 0
    assert {error.code for error in result.errors} == {"not_admitted"}
    assert state["calls"] == []


def test_environment_credential_loss_stays_distinct_from_explicit_disable(environment, monkeypatch):
    client, store, _, state = environment
    instance = json.loads(store.provider_credential_snapshot("searxng").values["baseUrls"])[0]
    store.save_provider_config("searxng", {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-clear", "clearCredentials": True,
    })
    assert {error.code for error in read(client).errors} == {"not_admitted"}
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig(searxng_base_urls=[instance]))
    store.save_provider_config("searxng", {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-environment-enable", "enabled": True,
    })
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    result = read(client)
    assert result.upstreamAttempts == 0 and {error.code for error in result.errors} == {"missing_credentials"}
    assert state["calls"] == []


def test_cache_expiry_and_exhausted_budget_do_not_fabricate_fresh_reads(environment):
    from src.services.thesis_ledger_news_cache import news_replay_cache
    client, _, runtime, state = environment
    first = read(client)
    assert first.status == "available"
    tiny = read(client, payload(requestBudgetMs=1))
    assert tiny.status == "unavailable" and tiny.upstreamAttempts == 0
    assert {error.code for error in tiny.errors} == {"timeout"}
    assert len(state["calls"]) == 1
    cache = news_replay_cache(runtime)
    now = cache.clock()
    cache.clock = lambda: now + 61
    second = read(client)
    assert second.status == "available" and second.fetchedAt != first.fetchedAt
    assert len(state["calls"]) == 2


def test_nonempty_environment_revision_change_invalidates_policy_and_cached_read(environment, monkeypatch):
    client, store, _, state = environment
    instance = json.loads(store.provider_credential_snapshot("searxng").values["baseUrls"])[0]
    store.save_provider_config("searxng", {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-to-env", "clearCredentials": True,
    })
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig(searxng_base_urls=[instance]))
    store.save_provider_config("searxng", {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-env-enable", "enabled": True,
    })
    admit(store)
    assert read(client).status == "available" and len(state["calls"]) == 1
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig(searxng_base_urls=[instance + "/changed"]))
    target = store.effective_policy_v3()["routes"][0]["targets"][0]
    assert target["eligible"] is False and target["reason"] == "not_admitted"
    result = read(client)
    assert result.status == "unavailable" and result.upstreamAttempts == 0
    assert {error.code for error in result.errors} == {"not_admitted"} and len(state["calls"]) == 1
