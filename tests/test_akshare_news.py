"""现有 SDK 新闻的传输、元数据和正式准入；全部使用受控响应。"""

from datetime import datetime, timedelta, timezone
import json
import time
import logging

import pandas as pd
import pytest

from data_provider import akshare_news as news
from data_provider.thesis_ledger_sources import akshare_news as source
from src import data_config
from src.data_config import DataServiceConfig
from src.services.thesis_ledger_control import ThesisLedgerControlStore
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
TARGET = {"providerId": "akshare", "upstreamSource": "eastmoney"}


def frame(**changes):
    return pd.DataFrame([{"关键词": "600519", "新闻标题": "公开新闻", "新闻内容": "不复制正文",
                          "发布时间": "2026-10-21 16:26:00", "文章来源": "证券时报网",
                          "新闻链接": "http://finance.eastmoney.com/a/202610213902666262.html", **changes}])


class Connection:
    def __init__(self):
        self.events = []
        self.closed = False

    def send(self, event):
        self.events.append(event)

    def close(self):
        self.closed = True


def test_metadata_timezone_and_reprint_identity_are_preserved():
    row, = news.project_news(frame(), "600519")
    assert row.title == "公开新闻" and row.publisher == "证券时报网"
    assert row.published_at == "2026-10-21T16:26:00+08:00"
    assert row.url == "https://finance.eastmoney.com/a/202610213902666262.html"
    assert row.source_item_id == "202610213902666262"
    assert row.excerpt is None and row.original_url is None and row.source_available_at is None


@pytest.mark.parametrize("value", [None, "", "invalid", "2026-1-2 03:04:05", "2026-10-21T08:26:00Z"])
def test_unknown_timestamp_is_not_replaced_by_fetch_time(value):
    assert news.project_news(frame(发布时间=value), "600519")[0].published_at is None


@pytest.mark.parametrize("change", [
    {"关键词": "000001"}, {"新闻标题": "<script>unsafe</script>"}, {"新闻标题": ""},
    {"新闻链接": "https://finance.eastmoney.com.evil.test/a/202610213902666262.html"},
    {"新闻链接": "https://person:secret@finance.eastmoney.com/a/202610213902666262.html"},
    {"新闻链接": "https://finance.eastmoney.com:444/a/202610213902666262.html"},
    {"新闻链接": "javascript:alert(1)"},
])
def test_wrong_identity_title_and_link_fail_closed(change):
    with pytest.raises(ValueError):
        news.project_news(frame(**change), "600519")


def test_changed_columns_or_page_size_are_rejected():
    for changed in [frame().drop(columns=["关键词"]), pd.concat([frame()] * 11)]:
        with pytest.raises(ValueError):
            news.project_news(changed, "600519")


@pytest.mark.parametrize("status, failure", [(200, None), (302, None), (429, None), (200, "size"), (200, "timeout")])
def test_real_sdk_uses_bounded_cookie_free_transport(monkeypatch, status, failure):
    calls = []
    payload = {"result": {"cmsArticleWebOld": [{
        "code": "202610213902666262", "date": "2026-10-21 16:26:00", "title": "公开<em>新闻</em>",
        "mediaName": "证券时报网", "content": "不复制正文", "image": "ignored",
    }]}}
    raw = ("jQuery35101792940631092459_1764599530165(" + json.dumps(payload) + ")").encode()

    class Response:
        status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def close(self):
            pass

        def iter_content(self, **_kwargs):
            if failure == "timeout":
                from curl_cffi import requests
                raise requests.exceptions.Timeout("受控读取超时")
            if failure == "size":
                yield b"x" * (news.MAX_BYTES + 1)
                return
            yield raw

    class Session(Response):
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    from curl_cffi import requests
    monkeypatch.setattr(requests, "Session", Session)
    from akshare.news import news_stock
    original = news_stock.requests.get
    connection = Connection()
    news.akshare_news_worker(connection, news.AkshareNewsSettings(), "600519.SH", 2, 3, time.monotonic() + 5)
    assert len(calls) == 1 and calls[0][0] == news.SEARCH_URL
    assert calls[0][1]["allow_redirects"] is False and 0 < calls[0][1]["timeout"] <= 5
    assert "cookie" not in {key.lower() for key in calls[0][1]["headers"]}
    assert news_stock.requests.get is original
    assert connection.events.count(("attempt", None)) == 1 and connection.closed
    result = [value for kind, value in connection.events if kind == "result"][0]
    assert result["success"] is (status == 200 and failure is None) and result["truncated"]
    if status == 200 and failure is None:
        assert result["records"][0].title == "公开新闻"
    else:
        assert result["records"] == () and ("http_status", status) in connection.events
    if failure == "timeout":
        assert ("timeout", None) in connection.events


def test_unreviewed_sdk_is_rejected_before_attempt_and_restore(monkeypatch):
    import akshare
    monkeypatch.setattr(akshare, "__version__", "unreviewed")
    connection = Connection()
    news.akshare_news_worker(connection, news.AkshareNewsSettings(), "600519.SH", 5, 3, time.monotonic() + 5)
    assert not any(kind == "attempt" for kind, _ in connection.events)
    assert [value for kind, value in connection.events if kind == "result"][0]["success"] is False


def controlled_worker(connection, _settings, _query, _limit, _days, _deadline):
    row, = news.project_news(frame(), "600519")
    connection.send(("attempt", None))
    connection.send(("result", {"records": (row,), "success": True, "truncated": True,
                                "examined_count": 1, "partial": False}))
    connection.close()


def test_cursor_and_exhausted_budget_make_no_attempt():
    for options in [{"cursor": "unsupported", "request_budget_ms": 5000}, {"request_budget_ms": 100}]:
        result = execute_news_search(news.AkshareNewsSettings(), "600519.SH", limit=5, days=3,
                                     worker=controlled_worker, **options)
        assert not result.success and result.upstream_attempts == 0


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "controlled-akshare-news-test-only")
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    monkeypatch.setattr(source, "akshare_news_worker", controlled_worker)
    from src.services import thesis_ledger_news_instruments as identities
    monkeypatch.setattr(identities, "_index_rows", lambda: [
        ["600519.SH", "600519", "贵州茅台", "", "", [], "CN", "stock", True],
        ["000001.SZ", "000001", "平安银行", "", "", [], "CN", "stock", True],
    ])
    store = ThesisLedgerControlStore(str(tmp_path / "news.db"))
    return ThesisLedgerProviderRuntime(store), store


def admit(store):
    now = datetime.now(timezone.utc)
    store.record_route_admission_v3(
        key=KEY, target=TARGET, evidence_ref="controlled-akshare-news-test", evidence_sha256="b" * 64,
        scope_symbols=["600519.SH"], scope_date_from="2026-10-18", scope_date_to="2026-10-28",
        adapter_revision=source.ADAPTER_REVISION, source_revision=source.SOURCE_REVISION,
        credential_revision="not-required", valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=(now + timedelta(hours=1)).isoformat(), recorded_by="controlled-test",
    )


def request(**updates):
    return NewsRequest.model_validate({"requestId": "controlled-akshare-news", "instrument": {
        "symbol": "600519.SH", "market": "CN", "assetType": "STOCK"},
        "publishedStart": "2026-10-18T00:00:00Z", "publishedEnd": "2026-10-21T19:00:00Z", **updates})


def test_first_use_and_exact_binding_preserve_public_identity(runtime):
    app, store = runtime
    pending = execute_news_request(app, request())
    assert pending.status == "unavailable" and pending.upstreamAttempts == 0
    from src.services.thesis_ledger_capability_verification import VerificationCatalog
    assert VerificationCatalog(store).read(KEY, TARGET) == {"state": "unverified"}
    admit(store)
    applied = store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "local-policy",
                                    "revision": 1, "enabled": True,
                                    "routes": [{"key": KEY, "targets": [TARGET], "enabled": True}]})
    assert applied["status"] == "applied"
    result = execute_news_request(app, request())
    assert result.status == "partial" and result.upstreamAttempts == 1 and len(result.items) == 1
    assert not result.coverage.complete and "search_first_page_not_exhaustive" in result.coverage.missingReasons
    assert result.route.providerId == "akshare" and result.route.upstreamSource == "eastmoney"
    other = execute_news_request(app, request(instrument={"symbol": "000001.SZ", "market": "CN", "assetType": "STOCK"}))
    assert other.status == "partial" and other.upstreamAttempts == 1
    rejected = execute_news_request(app, request(instrument={"symbol": "999999.SH", "market": "CN", "assetType": "STOCK"}))
    assert rejected.status == "unavailable" and rejected.upstreamAttempts == 0


def test_stale_revision_blocks_policy_and_manifest_is_exact(runtime):
    app, store = runtime
    admit(store)
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    definition = CAPABILITY_SOURCES.source("akshare")
    manifest = definition.manifest()
    assert [row["sourceId"] for row in manifest["upstreamSources"] if "NEWS_SEARCH" in row["capabilities"]] == ["eastmoney"]
    row = store.get_route_admission_v3(key=KEY, target=TARGET)
    assert app._current_market_v3_admission(KEY, TARGET) is not None
    row["sourceRevision"] = "stale"
    from types import SimpleNamespace
    context = SimpleNamespace(key=KEY, target=TARGET, manifest=manifest, state="admitted", payload=row)
    assert not source.admission_current(context)
