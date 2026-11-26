"""Yahoo Search.news 元数据、隔离网络与精确准入，不访问真实上游。"""

from datetime import datetime, timedelta, timezone
import json
import time
from types import SimpleNamespace

import pytest

from data_provider import yahoo_news as news, yahoo_news_transport as transport
from data_provider.thesis_ledger_sources import yahoo_news as source, yfinance as yahoo_source
from src import data_config
from src.data_config import DataServiceConfig
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_news_contract_v1 import NewsRequest
from src.services.thesis_ledger_news_gateway import execute_news_request
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime

KEY = {"kind": "data", "market": "US", "assetType": "STOCK", "capability": "NEWS_SEARCH"}
TARGET = {"providerId": "yfinance", "upstreamSource": source.UPSTREAM}


def payload(**changes):
    return {"quotes": [{"symbol": "NVDA", "quoteType": "EQUITY"}], "news": [{
        "uuid": "controlled-yahoo", "title": "公开新闻标题", "link": "https://finance.yahoo.com/news/story.html",
        "publisher": "公开媒体", "providerPublishTime": 1792645918, "relatedTickers": ["NVDA", "AAPL"],
        "summary": "不复制正文", **changes}]}


def body(**changes):
    return json.dumps(payload(**changes), ensure_ascii=False).encode()


class Connection:
    def __init__(self):
        self.events = []

    def send(self, value):
        self.events.append(value)

    def close(self):
        pass


def test_source_metadata_seconds_and_review_projection():
    page = news.parse_yahoo_page(body(), "NVDA.US", max_results=5)
    row, = page.records
    assert datetime.fromisoformat(row.published_at).timestamp() == 1792645918
    assert row.publisher == "公开媒体" and row.source_item_id == "controlled-yahoo"
    assert row.excerpt is None and row.original_url is None and row.source_available_at is None
    assert set(page.metadata["payload"]["news"][0]) == news.ITEM_FIELDS
    assert "summary" not in json.dumps(page.metadata)


@pytest.mark.parametrize("value", [None, True, "1792645918", -1, 1792645918000, 10 ** 30])
def test_unknown_or_millisecond_timestamp_is_not_invented(value):
    row, = news.parse_yahoo_page(body(providerPublishTime=value), "NVDA.US", max_results=5).records
    assert row.published_at is None


@pytest.mark.parametrize("changes", [
    {"relatedTickers": ["AAPL"]}, {"relatedTickers": None}, {"relatedTickers": ["NVDA", 1]},
    {"uuid": ""}, {"title": ""}, {"title": "<script>unsafe</script>"},
    {"link": "http://finance.yahoo.com/news/story"}, {"link": "https://person:secret@finance.yahoo.com/news"},
    {"link": "javascript:alert(1)"},
])
def test_unrelated_identity_and_unsafe_metadata_are_rejected(changes):
    with pytest.raises(ValueError):
        news.parse_yahoo_page(body(**changes), "NVDA.US", max_results=5)


@pytest.mark.parametrize("quotes", [[], [None], [{"symbol": "AAPL", "quoteType": "EQUITY"}],
                                    [{"symbol": "NVDA", "quoteType": "ETF"}]])
def test_search_quote_must_match_stock_identity(quotes):
    with pytest.raises(ValueError):
        news.parse_yahoo_page(json.dumps({**payload(), "quotes": quotes}).encode(), "NVDA.US", max_results=5)


def test_empty_valid_search_is_distinct_from_invalid_envelope_and_overflow():
    assert news.parse_yahoo_page(json.dumps({**payload(), "news": []}).encode(), "NVDA.US", max_results=5).records == ()
    for raw in [b"[]", b"{}", b"broken", b"x" * (transport.MAX_BYTES + 1),
                json.dumps({**payload(), "news": payload()["news"] * 6}).encode()]:
        with pytest.raises(ValueError):
            news.parse_yahoo_page(raw, "NVDA.US", max_results=5)


def search_params():
    return {"q": "NVDA", "quotesCount": 1, "newsCount": 5, "enableFuzzyQuery": False,
            "listsCount": 0, "recommendedCount": 0, "enableCb": False, "enableNavLinks": False,
            "enableResearchReports": False, "enableCulturalAssets": False}


@pytest.mark.parametrize("status,expected", [(200, None), (404, "upstream_failure"), (429, "rate_limited")])
def test_search_http_errors_do_not_become_successful_empty_results(monkeypatch, status, expected):
    sent, closed = [], []

    def send(_session, method, url, **kwargs):
        sent.append((method, url, kwargs))
        return SimpleNamespace(status_code=status, iter_content=lambda: iter([body()]),
                               close=lambda: closed.append(True))

    monkeypatch.setattr(transport.requests.Session, "request", send)
    connection = Connection()
    with transport.YahooNewsSession(connection, time.monotonic() + 3, "NVDA", 5) as session:
        if expected:
            with pytest.raises(transport.YahooNewsError) as error:
                session.get(transport.SEARCH_URL, params=search_params())
            assert error.value.code == expected and session.search_body is None
        else:
            session.get(transport.SEARCH_URL, params=search_params())
            assert session.search_body == body()
        assert sent[0][2]["allow_redirects"] is False and sent[0][2]["stream"] is True
        assert 0 < sent[0][2]["timeout"] <= 3 and len(closed) == 1
    assert connection.events == [("attempt", None), ("http_status", status)]


@pytest.mark.parametrize("url", ["https://other.test/", "http://fc.yahoo.com/", "https://fc.yahoo.com:443/",
                                 "https://person:secret@fc.yahoo.com/", "https://fc.yahoo.com/?crumb=secret"])
def test_transport_rejects_other_targets_without_network(url):
    connection = Connection()
    with transport.YahooNewsSession(connection, time.monotonic() + 1, "NVDA", 5) as session:
        with pytest.raises(transport.YahooNewsError):
            session.get(url)
        with pytest.raises(transport.YahooNewsError):
            session.post(transport.SEARCH_URL)
        with pytest.raises(transport.YahooNewsError):
            session.get(transport.SEARCH_URL, params={**search_params(), "q": "AAPL"})
    assert connection.events == []


def test_bootstrap_status_is_not_news_failure_and_request_size_count_deadline_are_bounded(monkeypatch):
    size = {"body": b"cookie-bootstrap"}
    monkeypatch.setattr(transport.requests.Session, "request", lambda *_a, **_k: SimpleNamespace(
        status_code=404, iter_content=lambda: iter([size["body"]]), close=lambda: None))
    connection = Connection()
    with transport.YahooNewsSession(connection, time.monotonic() + 2, "NVDA", 5) as session:
        for _ in range(transport.MAX_HTTP_REQUESTS):
            session.get("https://fc.yahoo.com")
        with pytest.raises(transport.YahooNewsError):
            session.get("https://fc.yahoo.com")
        assert len(connection.events) == transport.MAX_HTTP_REQUESTS
        assert all(kind == "attempt" for kind, _ in connection.events)
    size["body"] = b"x" * (transport.MAX_BYTES + 1)
    with transport.YahooNewsSession(Connection(), time.monotonic() + 2, "NVDA", 5) as session:
        with pytest.raises(transport.YahooNewsError):
            session.get("https://fc.yahoo.com")
    with transport.YahooNewsSession(Connection(), time.monotonic() - 1, "NVDA", 5) as session:
        with pytest.raises(transport.requests.exceptions.Timeout):
            session.get("https://fc.yahoo.com")


def test_uses_search_news_with_no_persistent_cache_and_restores_sdk_settings(monkeypatch):
    import yfinance as yf
    import yfinance.cache as cache
    from yfinance.config import YfConfig
    before = (cache._CookieCacheManager._Cookie_cache, cache._TzCacheManager._tz_cache, YfConfig.network.retries)

    def search(ticker, **kwargs):
        assert ticker == "NVDA" and kwargs["max_results"] == 1 and kwargs["news_count"] == 5
        assert kwargs["lists_count"] == 0 and kwargs["recommended"] == 0 and kwargs["include_cb"] is False
        assert kwargs["enable_fuzzy_query"] is False and kwargs["raise_errors"] is True
        assert isinstance(cache.get_cookie_cache(), cache._CookieCacheDummy)
        assert isinstance(cache.get_tz_cache(), cache._TzCacheDummy) and YfConfig.network.retries == 0
        kwargs["session"].search_body = body()
        return SimpleNamespace(news=payload()["news"])

    monkeypatch.setattr(yf, "Search", search)
    assert len(news.fetch_yahoo_news("NVDA.US", Connection(), time.monotonic() + 5, max_results=5).records) == 1
    assert before == (cache._CookieCacheManager._Cookie_cache, cache._TzCacheManager._tz_cache, YfConfig.network.retries)
    monkeypatch.setattr(yf, "__version__", "unreviewed")
    with pytest.raises(transport.YahooNewsError):
        news.fetch_yahoo_news("NVDA.US", Connection(), time.monotonic() + 5, max_results=5)


def controlled_worker(connection, *_args):
    value = payload()
    value["news"].append({**value["news"][0], "uuid": "repost", "link": "https://finance.yahoo.com/news/repost.html"})
    page = news.parse_yahoo_page(json.dumps(value).encode(), "NVDA.US", max_results=5)
    connection.send(("attempt", None))
    connection.send(("result", {"records": page.records, "success": True, "truncated": False,
                                "examined_count": 2, "partial": False}))
    connection.close()


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    monkeypatch.setattr(source, "yahoo_news_worker", controlled_worker)
    from src.services import thesis_ledger_news_instruments as identities
    monkeypatch.setattr(identities, "_index_rows", lambda: [
        [ticker, ticker, name, "", "", [], "US", "stock", True]
        for ticker, name in [("NVDA", "NVIDIA"), ("AAPL", "Apple")]
    ])
    store = ThesisLedgerControlStore(str(tmp_path / "yahoo-news.db"))
    return ThesisLedgerProviderRuntime(store), store


def request(**changes):
    return NewsRequest.model_validate({"requestId": "controlled-yahoo-news", "instrument": {
        "symbol": "NVDA.US", "market": "US", "assetType": "STOCK"},
        "publishedStart": "2026-10-19T00:00:00Z", "publishedEnd": "2026-10-23T00:00:00Z", **changes})


def test_exact_news_binding_preserves_old_market_sources():
    manifest = yahoo_source.manifest()
    for upstream in manifest["upstreamSources"]:
        if upstream["sourceId"] == source.UPSTREAM:
            assert upstream["capabilities"] == {"NEWS_SEARCH": ["STOCK"]}
        else:
            assert "NEWS_SEARCH" not in upstream["capabilities"]
    assert source.BINDING.gated and source.BINDING.admission_required


def test_first_use_keeps_revision_cursor_identity_and_disable_boundaries(runtime):
    app, store = runtime
    first_use = execute_news_request(app, request())
    assert first_use.status == "unavailable" and first_use.upstreamAttempts == 0
    from src.services.thesis_ledger_capability_verification import VerificationCatalog
    assert VerificationCatalog(store).read(KEY, TARGET) == {"state": "unverified"}
    now = datetime.now(timezone.utc)
    store.record_route_admission_v3(
        key=KEY, target=TARGET, evidence_ref="controlled-yahoo-news", evidence_sha256="e" * 64,
        scope_symbols=["NVDA.US"], scope_date_from="2026-10-19", scope_date_to="2026-10-29",
        adapter_revision=source.ADAPTER_REVISION, source_revision=source.SOURCE_REVISION,
        credential_revision="not-required", valid_from=(now - timedelta(minutes=1)).isoformat(),
        valid_until=(now + timedelta(hours=1)).isoformat(), recorded_by="controlled-test")
    store.apply_policy_v3({"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "yahoo-news-policy",
                           "revision": 1, "enabled": True,
                           "routes": [{"key": KEY, "targets": [TARGET], "enabled": True}]})
    value = execute_news_request(app, request())
    assert value.status == "partial" and value.upstreamAttempts == 1, value.model_dump()
    assert len(value.items) == value.coverage.uniqueEvidenceCount == 1
    assert len(value.items[0].sourceUrls) == 2 and value.items[0].originalUrl is None
    assert value.route.providerId == "yfinance" and "search_first_page_not_exhaustive" in value.coverage.missingReasons
    admission = store.get_route_admission_v3(key=KEY, target=TARGET)
    context = {"key": KEY, "target": TARGET, "manifest": yahoo_source.manifest(), "state": "admitted"}
    assert source.admission_current(SimpleNamespace(**context, payload=admission))
    for field in ["adapterRevision", "sourceRevision", "credentialRevision"]:
        assert not source.admission_current(SimpleNamespace(**context, payload={**admission, field: "stale"}))
    for change in [{"instrument": {"symbol": "UNKNOWN.US", "market": "US", "assetType": "STOCK"}},
                   {"cursor": "next"}]:
        assert execute_news_request(app, request(**change)).upstreamAttempts == 0
    other = execute_news_request(app, request(instrument={"symbol": "AAPL.US", "market": "US", "assetType": "STOCK"}))
    assert other.status == "partial" and other.upstreamAttempts == 1
    historical = execute_news_request(app, request(publishedStart="2020-01-01T00:00:00Z", publishedEnd="2020-01-02T00:00:00Z"))
    assert historical.status == "partial" and historical.upstreamAttempts == 1 and historical.items == []
    store.save_provider_config("yfinance", {"requestId": "yahoo-disable", "enabled": False})
    assert execute_news_request(app, request()).upstreamAttempts == 0
