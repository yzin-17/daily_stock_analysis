"""新闻来源响应形状、网络目标及启动失败边界。"""

import json
import time
from types import SimpleNamespace

import pytest
import requests

from src.services import thesis_ledger_news_execution as execution
from src.services.thesis_ledger_news_sources import NewsSearchSettings, PROVIDERS, build_news_source
from src.services.thesis_ledger_news_transport import NewsTransportObserver


def response(payload):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(payload).encode()
    return result


def envelope(provider, rows):
    if provider == "bocha":
        return {"data": {"webPages": {"value": rows}}}
    if provider == "brave":
        return {"web": {"results": rows}}
    if provider == "minimax":
        return {"organic": rows}
    if provider == "serpapi":
        return {"organic_results": rows}
    return {"results": rows}


@pytest.mark.parametrize("provider", PROVIDERS)
def test_declared_source_payload_empty_invalid_and_original_metadata(provider):
    observer = NewsTransportObserver(None, NewsSearchSettings(provider), time.monotonic() + 10)
    observer.observe_payload(response({}))
    assert observer.invalid_payload and not observer.valid_payload
    observer.observe_payload(response(envelope(provider, [])))
    assert observer.valid_payload and observer.examined_count == 0
    row = {"url": "https://news.example/item", "title": "原文", "id": "source-id",
           "publishedDate": "2026-10-18T01:00:00Z", "sourceAvailableAt": "2026-10-18T02:00:00Z",
           "originalUrl": "https://publisher.example/original"}
    wire = response(envelope(provider, [row]))
    observer.observe_payload(wire)
    observer.observe_payload(wire)
    assert observer.examined_count == 1
    source_result = SimpleNamespace(url=row["url"], title="原文", snippet="未执行的源文",
                                    source="news.example", published_date=None)
    record, = observer.records([source_result])
    assert record.published_at == row["publishedDate"]
    assert record.source_available_at == row["sourceAvailableAt"]
    assert record.original_url == row["originalUrl"] and record.source_item_id == "source-id"


@pytest.mark.parametrize("provider", [key for key in PROVIDERS if key != "searxng"])
def test_vendor_transport_never_permits_another_vendor_or_plain_http(provider):
    settings = NewsSearchSettings(provider, api_keys=("controlled-test-value",))
    host = PROVIDERS[provider][1]
    assert settings.permits_url(f"https://{host}/search")
    assert not settings.permits_url(f"http://{host}/search")
    assert not settings.permits_url(f"https://{host}.example/search")


def test_searxng_uses_only_explicit_instance_and_never_discovers_public_instances(monkeypatch):
    from src import search_service
    monkeypatch.setattr(search_service.SearXNGSearchProvider, "_get_public_instances",
                        lambda *_: pytest.fail("不应发现公共实例"))
    settings = NewsSearchSettings("searxng", base_urls=("https://search.example:8443/base",))
    source = build_news_source(settings)
    assert source._base_urls == ["https://search.example:8443/base"]
    assert source._use_public_instances is False
    assert settings.permits_url("https://search.example:8443/base/search")
    assert not settings.permits_url("https://search.example/base/search")
    assert not settings.permits_url("https://other.example:8443/search")


def test_failed_process_start_releases_slot_without_claiming_upstream_attempt(monkeypatch):
    settings = NewsSearchSettings("searxng", base_urls=("https://search.example",))
    result = execution.execute_news_search(settings, "公开标的", limit=5, days=3,
                                           request_budget_ms=10_000, worker=lambda *_: None)
    assert not result.success and result.upstream_attempts == 0
    assert {error.code for error in result.errors} == {"unsupported"}
    acquired = []
    try:
        for _ in range(4):
            acquired.append(execution._NEWS_WORKER_SLOTS.acquire(blocking=False))
        assert all(acquired)
    finally:
        for value in acquired:
            if value:
                execution._NEWS_WORKER_SLOTS.release()


def test_concurrency_limit_never_starts_another_execution():
    settings = NewsSearchSettings("searxng", base_urls=("https://search.example",))
    for _ in range(4):
        assert execution._NEWS_WORKER_SLOTS.acquire(blocking=False)
    try:
        result = execution.execute_news_search(settings, "公开标的", limit=5, days=3,
                                               request_budget_ms=10_000)
        assert result.upstream_attempts == 0
        assert {error.code for error in result.errors} == {"rate_limited"}
    finally:
        for _ in range(4):
            execution._NEWS_WORKER_SLOTS.release()
