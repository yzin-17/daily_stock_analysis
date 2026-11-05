"""主备检索走真实受控 HTTP，共用一个请求预算。"""

from dataclasses import replace
import time
from urllib.parse import urlsplit

import requests

from data_provider.thesis_ledger_sources import news as news_source
from src.services.thesis_ledger_news_execution import execute_news_search, news_search_worker
from src.services.thesis_ledger_news_service import NEWS_UPSTREAMS
from tests import test_thesis_ledger_news_gateway as gateway_tests

server = gateway_tests.server
environment = gateway_tests.environment


def controlled_brave_worker(connection, settings, *args):
    """仅测试：保留原 Adapter 请求，在传输末端改送本地受控 HTTP。"""
    original = requests.sessions.Session.send

    def send(session, request, **kwargs):
        target = urlsplit(request.url)
        if target.hostname != "api.search.brave.com":
            raise requests.RequestException("测试不允许其他网络目标")
        copied = request.copy()
        copied.url = settings.base_urls[0] + target.path + ("?" + target.query if target.query else "")
        kwargs["proxies"] = {}
        return original(session, copied, **kwargs)

    requests.sessions.Session.send = send
    try:
        news_search_worker(connection, settings, *args)
    finally:
        requests.sessions.Session.send = original


def configure_fallback(environment, monkeypatch):
    client, store, runtime, state = environment
    origin = news_source.search_news
    local_url = gateway_tests.json.loads(store.provider_credential_snapshot("searxng").values["baseUrls"])[0]

    def selected(query, route, **kwargs):
        def execute(settings, *args, **options):
            if settings.provider_id == "brave":
                settings = replace(settings, base_urls=(local_url,))
                options["worker"] = controlled_brave_worker
            return execute_news_search(settings, *args, **options)
        return origin(query, route, executor=execute, **kwargs)

    monkeypatch.setattr(news_source, "search_news", selected)
    target = {"providerId": "brave", "upstreamSource": NEWS_UPSTREAMS["brave"]}
    gateway_tests.enable(store, "brave", {"apiKeys": '["controlled-brave-test-key"]'})
    gateway_tests.admit(store, target)
    gateway_tests.apply(store, revision=2, targets=[gateway_tests.TARGET, target])
    return client, runtime, state


def test_declared_backup_keeps_actual_attempts_errors_and_does_not_cache_partial_success(environment, monkeypatch):
    client, _, state = configure_fallback(environment, monkeypatch)
    state["mode"] = "primary-rate"
    result = gateway_tests.read(client)
    assert result.status == "partial" and result.route.providerId == "brave"
    assert result.upstreamAttempts == len(state["calls"]) == 2
    assert state["paths"] == ["/search", "/res/v1/web/search"]
    assert result.errors[0].code == "rate_limited" and result.errors[0].providerId == "searxng"
    assert "fallback_after_failure" in result.coverage.missingReasons and result.coverage.complete is False
    assert gateway_tests.read(client).status == "partial" and len(state["calls"]) == 4


def test_timeout_budget_does_not_restart_for_backup_and_leaves_no_upstream_work(environment, monkeypatch):
    client, _, state = configure_fallback(environment, monkeypatch)
    state["mode"] = "slow"
    started = time.monotonic()
    result = gateway_tests.read(client, gateway_tests.payload(requestBudgetMs=5_000))
    assert time.monotonic() - started < 5
    assert result.status == "unavailable" and result.upstreamAttempts == len(state["calls"]) == 1
    assert state["paths"] == ["/search"]
    assert {error.code for error in result.errors} == {"timeout"}
