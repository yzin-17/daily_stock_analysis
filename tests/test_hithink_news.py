"""HiThink 新闻元数据、真实错误归类与当前凭据准入；不访问外部网络。"""

from datetime import datetime, timedelta, timezone
import json
import time
import logging

import pytest

from data_provider import hithink_news as news
from data_provider.thesis_ledger_sources import hithink_news as source
from src import data_config
from src.data_config import DataServiceConfig
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_news_contract_v1 import NewsRequest
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_gateway import execute_news_request
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


@pytest.fixture(autouse=True)
def restore_worker_logging():
    """在当前进程测试子进程入口时，恢复其全局日志开关。"""
    disabled = logging.root.manager.disable
    try:
        yield
    finally:
        logging.disable(disabled)

KEY = {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": "NEWS_SEARCH"}
TARGET = {"providerId": "hithink", "upstreamSource": source.UPSTREAM}
TEST_KEY = "controlled-hithink-news-key"


def body(*, code=0, has_more=True, **item_changes):
    return json.dumps({"code": code, "data": {"items": [{
        "id": "1_controlled", "title": "公开新闻标题", "source": "公开媒体", "publish_time_ms": 1792645918000,
        "jump_url": "https://news.10jqka.com.cn/20261022/c680969463.shtml", "summary": "不复制正文", **item_changes,
    }], "has_more": has_more, "offset": "opaque-next"}}, ensure_ascii=False).encode()


class Connection:
    def __init__(self):
        self.events = []
        self.closed = False

    def send(self, event):
        self.events.append(event)

    def close(self):
        self.closed = True


def test_metadata_and_millisecond_clock_keep_reprint_and_availability_unknown():
    page = news.parse_hithink_page(body(), max_results=5)
    row, = page.records
    assert datetime.fromisoformat(row.published_at).timestamp() * 1000 == 1792645918000
    assert row.title == "公开新闻标题" and row.publisher == "公开媒体"
    assert row.original_url is None and row.excerpt is None and row.source_available_at is None
    assert page.has_more is True and page.offset == "opaque-next"
    assert TEST_KEY not in repr(news.HithinkNewsSettings(TEST_KEY))


@pytest.mark.parametrize("value", [None, "1792645918000", True, -1, 10 ** 30])
def test_unknown_or_invalid_clock_stays_unknown(value):
    assert news.parse_hithink_page(body(publish_time_ms=value), max_results=5).records[0].published_at is None


@pytest.mark.parametrize("change", [
    {"id": 123}, {"title": "<script>unsafe</script>"}, {"title": ""},
    {"jump_url": "https://news.10jqka.com.cn.evil.test/news"},
    {"jump_url": "https://person:secret@news.10jqka.com.cn/news"},
    {"jump_url": "https://news.10jqka.com.cn:444/news"}, {"jump_url": "javascript:alert(1)"},
])
def test_wrong_identity_title_and_link_are_rejected(change):
    with pytest.raises(news.HithinkNewsError):
        news.parse_hithink_page(body(**change), max_results=5)


def test_envelope_limit_and_pagination_are_checked():
    invalid = [b"[]", b"{}", b"broken", b"x" * (news.MAX_BYTES + 1), body(code=False)]
    payload = json.loads(body())
    payload["data"]["offset"] = None
    invalid.append(json.dumps(payload).encode())
    payload["data"]["offset"] = "next"
    payload["data"]["items"] *= 6
    invalid.append(json.dumps(payload).encode())
    for value in invalid:
        with pytest.raises((ValueError, news.HithinkNewsError)):
            news.parse_hithink_page(value, max_results=5)


@pytest.mark.parametrize("status, code, failure", [
    (200, 0, None), (302, 0, None), (401, 0, None), (403, 0, None), (429, 0, None),
    (200, 4001, None), (200, 2001, None), (200, 0, "size"), (200, 0, "timeout"),
])
def test_fixed_endpoint_single_attempt_and_business_errors(monkeypatch, status, code, failure):
    calls = []

    class Response:
        status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_content(self, **_kwargs):
            if failure == "timeout":
                import requests
                raise requests.Timeout()
            yield b"x" * (news.MAX_BYTES + 1) if failure == "size" else body(code=code)

    class Session(Response):
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    import requests
    monkeypatch.setattr(requests, "Session", Session)
    connection = Connection()
    news.hithink_news_worker(connection, news.HithinkNewsSettings(TEST_KEY), "600519.SH", 5, 3, time.monotonic() + 5)
    assert len(calls) == 1 and calls[0][0] == news.NEWS_URL
    assert calls[0][1]["params"] == {"thscode": "600519.SH", "offset": "first", "size": 5}
    assert calls[0][1]["headers"]["X-api-key"] == TEST_KEY and calls[0][1]["allow_redirects"] is False
    assert connection.closed and connection.events.count(("attempt", None)) == 1
    assert ("http_status", status) in connection.events
    result = [v for k, v in connection.events if k == "result"][0]
    assert result["success"] is (status == 200 and code == 0 and failure is None)
    if code == 4001:
        assert ("rate_limited", None) in connection.events and ("http_status", 429) not in connection.events
    if failure == "timeout":
        assert ("timeout", None) in connection.events


def controlled_worker(connection, _settings, _query, _limit, _days, _deadline):
    page = news.parse_hithink_page(body(), max_results=5)
    connection.send(("attempt", None))
    connection.send(("result", {"records": page.records, "success": True, "truncated": page.has_more,
                                "examined_count": len(page.records), "partial": False}))
    connection.close()


def limited_worker(connection, *_args):
    connection.send(("attempt", None))
    connection.send(("http_status", 200))
    connection.send(("rate_limited", None))
    connection.send(("result", {"records": (), "success": False, "truncated": False,
                                "examined_count": 0, "partial": False}))
    connection.close()


def test_parent_preserves_business_rate_limit_without_inventing_http_status():
    value = execute_news_search(news.HithinkNewsSettings(TEST_KEY), "600519.SH", limit=5, days=3,
                                request_budget_ms=5000, worker=limited_worker)
    assert not value.success and value.upstream_attempts == 1
    assert [e.code for e in value.errors] == ["rate_limited"]


def configure(store, secret=TEST_KEY):
    store.save_provider_config("hithink", {"requestId": "controlled-news-config", "enabled": True,
                                          "credentials": {"method": "api_key", "values": {"apiKey": secret}}})


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "controlled-hithink-news-local-only")
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    monkeypatch.setattr(source, "hithink_news_worker", controlled_worker)
    from src.services import thesis_ledger_news_instruments as identities
    monkeypatch.setattr(identities, "_index_rows", lambda: [
        ["600519.SH", "600519", "贵州茅台", "", "", [], "CN", "stock", True],
        ["000001.SZ", "000001", "平安银行", "", "", [], "CN", "stock", True],
    ])
    store = ThesisLedgerControlStore(str(tmp_path / "news.db"))
    configure(store)
    return ThesisLedgerProviderRuntime(store), store


def request(**updates):
    return NewsRequest.model_validate({"requestId": "controlled-hithink-news", "instrument": {
        "symbol": "600519.SH", "market": "CN", "assetType": "STOCK"},
        "publishedStart": "2026-10-19T00:00:00Z", "publishedEnd": "2026-10-23T00:00:00Z", **updates})


def test_formal_hmac_admission_and_credential_change_make_no_unapproved_attempt(runtime):
    app, store = runtime
    assert execute_news_request(app, request()).upstreamAttempts == 0
    now = datetime.now(timezone.utc)
    revision = _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("hithink"))
    assert revision.startswith("hmac-sha256-v1:")
    store.record_route_admission_v3(
        key=KEY, target=TARGET, evidence_ref="controlled-hithink-news", evidence_sha256="c" * 64,
        scope_symbols=["600519.SH"], scope_date_from="2026-10-19", scope_date_to="2026-10-29",
        adapter_revision=source.ADAPTER_REVISION, source_revision=source.SOURCE_REVISION,
        credential_revision=revision, valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=(now + timedelta(hours=1)).isoformat(), recorded_by="controlled-test",
    )
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "controlled-news-policy",
                           "revision": 1, "enabled": True,
                           "routes": [{"key": KEY, "targets": [TARGET], "enabled": True}]})
    value = execute_news_request(app, request())
    assert value.status == "partial" and value.upstreamAttempts == 1 and len(value.items) == 1, value.model_dump()
    assert value.route.providerId == "hithink" and "timeline_window_not_exhaustive" in value.coverage.missingReasons
    assert TEST_KEY not in value.model_dump_json()
    for change in [{"instrument": {"symbol": "000001.SZ", "market": "CN", "assetType": "STOCK"}}, {"cursor": "next"}]:
        assert execute_news_request(app, request(**change)).upstreamAttempts == 0
    configure(store, "controlled-new-credential")
    denied = execute_news_request(app, request())
    assert denied.status == "unavailable" and denied.upstreamAttempts == 0
