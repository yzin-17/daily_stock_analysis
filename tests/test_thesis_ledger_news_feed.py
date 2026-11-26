"""市场快讯范围隔离、来源选择和准入撤销的正式入口验证。"""

from datetime import datetime, timedelta, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from api.thesis_ledger_news import build_news_router
from data_provider import market_flash_news as news
from data_provider.thesis_ledger_sources import market_flash_news as source
from src import data_config
from src.data_config import DataServiceConfig
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_news_feed_contract_v1 import NewsFeedRequest, NewsFeedResponse
from src.services.thesis_ledger_news_gateway import execute_news_request
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
from tests.test_market_flash_news import raw

KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "NEWS_FEED"}
TARGETS = [{"providerId": p, "upstreamSource": v[0]} for p, v in source.PROFILES.items()]


def controlled_worker(connection, settings, *_args):
    page = news.parse_flash_page(raw(settings.provider_id), settings.provider_id, max_results=5)
    connection.send(("attempt", None))
    connection.send(("result", {"records": page.records, "success": True, "truncated": False,
                                "examined_count": 1, "partial": False, "missing_reasons": page.missing_reasons}))
    connection.close()


def query(**changes):
    return NewsFeedRequest.model_validate({"requestId": "controlled-feed", "market": "CN", **changes})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    monkeypatch.setattr(source, "flash_news_worker", controlled_worker)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "controlled-feed-token")
    store = ThesisLedgerControlStore(str(tmp_path / "feed.db"))
    now = datetime.now(timezone.utc)
    for target in TARGETS:
        provider = target["providerId"]
        store.save_provider_config(provider, {"requestId": "controlled-enable", "enabled": True})
        store.record_route_admission_v3(
            key=KEY, target=target, evidence_ref="controlled-feed", evidence_sha256="f"*64,
            scope_symbols=["CN.STOCK"], scope_date_from=(now-timedelta(days=3)).date().isoformat(),
            scope_date_to=(now+timedelta(days=7)).date().isoformat(), adapter_revision=source.ADAPTER_REVISION,
            source_revision=source.PROFILES[provider][2], credential_revision="not-required",
            valid_from=(now-timedelta(minutes=1)).isoformat(), valid_until=(now+timedelta(hours=1)).isoformat(),
            recorded_by="controlled-test")
    store.apply_policy_v3({"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "controlled-policy",
                           "revision": 1, "enabled": True, "routes": [{"key": KEY, "targets": TARGETS, "enabled": True}]})
    return ThesisLedgerProviderRuntime(store), store


@pytest.mark.parametrize("provider", ["cls", "wallstreetcn"])
def test_market_feed_keeps_real_pinned_source_without_security_associations(runtime, provider):
    app, _store = runtime
    result = execute_news_request(app, query(providerId=provider))
    assert result.status == "partial" and result.upstreamAttempts == 1, result.model_dump()
    assert result.route.providerId == provider and result.market == "CN"
    assert result.coverage.basis == "current-feed" and "market_feed_first_page_not_exhaustive" in result.coverage.missingReasons
    assert len(result.items) == result.coverage.uniqueEvidenceCount == 1
    assert all(row.relatedInstruments == [] for row in result.items)
    assert "instrument" not in result.model_dump()
    bad = result.model_dump();bad["items"][0]["relatedInstruments"] = [{"symbol":"000001.SZ","market":"CN","assetType":"STOCK"}]
    with pytest.raises(ValidationError): NewsFeedResponse.model_validate(bad)


@pytest.mark.parametrize("changes", [{"providerId":"unconfigured"}, {"cursor":"next"}, {"requestBudgetMs":1}])
def test_unconfigured_scope_cursor_and_budget_reject_before_upstream(runtime, changes):
    result = execute_news_request(runtime[0], query(**changes))
    assert result.status == "unavailable" and result.upstreamAttempts == 0 and result.items == []


def test_past_window_first_use_preserves_missing_coverage(runtime):
    result = execute_news_request(runtime[0], query(publishedStart="2020-01-01T00:00:00Z", publishedEnd="2020-01-02T00:00:00Z"))
    assert result.status == "partial" and result.upstreamAttempts == 1 and result.items == []
    assert result.coverage.complete is False


@pytest.mark.parametrize("action", ["disable", "revoke"])
def test_explicit_source_cannot_fall_back_after_disable_or_revoke(runtime, action):
    app, store = runtime
    if action == "disable": store.save_provider_config("wallstreetcn", {"requestId":"controlled-off","enabled":False})
    else: store.revoke_route_admission_v3(key=KEY,target=TARGETS[1],reason="controlled-revoke")
    result = execute_news_request(app, query(providerId="wallstreetcn"))
    assert result.status == "unavailable" and result.upstreamAttempts == 0
    assert result.errors[0].code == "not_admitted"


def test_auth_and_stock_or_foreign_market_payloads_never_execute(runtime):
    app = FastAPI();app.include_router(build_news_router(lambda:runtime[0]),prefix="/api/v1/thesis-ledger")
    client = TestClient(app);path="/api/v1/thesis-ledger/market/news-feed"
    assert client.post(path,json=query().model_dump()).status_code == 401
    headers={"authorization":"Bearer controlled-feed-token"}
    for changes in [{"market":"US"},{"instrument":{"symbol":"000001.SZ","market":"CN","assetType":"STOCK"}}]:
        assert client.post(path,json={**query().model_dump(),**changes},headers=headers).status_code == 422
    result = client.post(path,json=query(providerId="wallstreetcn").model_dump(),headers=headers)
    assert result.status_code == 200 and result.json()["route"]["providerId"] == "wallstreetcn"
