"""官方有限订阅的边界、实际尝试与正式准入；不访问外部站点。"""

from datetime import datetime, timedelta, timezone
import time
import logging

import pytest

from data_provider import nvidia_news_rss as rss
from data_provider.thesis_ledger_sources import nvidia_news as source
from src import data_config
from src.data_config import DataServiceConfig
from src.services.thesis_ledger_capability_contracts import ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_news_contract_v1 import NewsRequest
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_gateway import execute_news_request
from src.services.thesis_ledger_news_records import RawNewsRecord
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


@pytest.fixture(autouse=True)
def restore_worker_logging():
    """在当前进程测试子进程入口时，恢复其全局日志开关。"""
    disabled = logging.root.manager.disable
    try:
        yield
    finally:
        logging.disable(disabled)

KEY = {"kind": "data", "market": "US", "assetType": "STOCK", "capability": "NEWS_SEARCH"}
TARGET = {"providerId": source.PROVIDER_ID, "upstreamSource": source.UPSTREAM}


def feed(*, url="https://nvidianews.nvidia.com/news/controlled-release", date="Wed, 21 Oct 2026 10:50:37 GMT"):
    return (f'<rss><channel><title>NVIDIA Newsroom</title><item><title>Official &amp; exact title</title>'
            f'<link>{url}</link><guid>release-1</guid><pubDate>{date}</pubDate>'
            '<description>Do not copy a full article.</description></item></channel></rss>').encode()


def request(**updates):
    return NewsRequest.model_validate({"requestId": "controlled-nvidia-news", "instrument": {
        "symbol": "NVDA.US", "market": "US", "assetType": "STOCK"}, **updates})


def controlled_worker(connection, _settings, _query, _max_results, _days, _deadline):
    now = datetime.now(timezone.utc)
    rows = tuple(RawNewsRecord(
        title=f"NVIDIA controlled release {i}", url=f"https://nvidianews.nvidia.com/news/controlled-{i}",
        original_url=f"https://nvidianews.nvidia.com/news/controlled-{i}", publisher="NVIDIA",
        source_item_id=f"controlled-{i}", published_at=(now - timedelta(days=i)).isoformat(),
    ) for i in range(4))
    connection.send(("attempt", None))
    connection.send(("result", {"records": rows, "success": True, "truncated": True,
                                "examined_count": len(rows), "partial": False}))
    connection.close()


class Connection:
    def __init__(self):
        self.events = []
        self.closed = False

    def send(self, event):
        self.events.append(event)

    def close(self):
        self.closed = True


def test_exact_fields_keep_unknown_source_time_and_do_not_copy_description():
    row, = rss.parse_nvidia_feed(feed())
    assert row.title == "Official & exact title"
    assert row.published_at == "2026-10-21T10:50:37+00:00"
    assert row.source_available_at is None and row.excerpt is None
    assert row.publisher == "NVIDIA" and row.source_item_id == "release-1"


@pytest.mark.parametrize("date", ["", "not-a-date", "Wed, 21 Oct 2026 10:50:37"])
def test_unknown_or_unzoned_date_is_not_replaced_by_fetch_time(date):
    assert rss.parse_nvidia_feed(feed(date=date))[0].published_at is None


@pytest.mark.parametrize("url", [
    "http://nvidianews.nvidia.com/news/a", "https://nvidianews.nvidia.com.evil.example/news/a",
    "https://person:secret@nvidianews.nvidia.com/news/a", "https://nvidianews.nvidia.com:8443/a",
])
def test_untrusted_links_fail_closed(url):
    with pytest.raises(ValueError):
        rss.parse_nvidia_feed(feed(url=url))


@pytest.mark.parametrize("body", [
    b'<!DOCTYPE rss [<!ENTITY x "unsafe">]><rss/>', b'<!ENTITY x "unsafe">',
    b'<html/>', b'<rss><channel><title>Other issuer</title></channel></rss>', b'<rss>',
])
def test_other_issuer_and_unsafe_or_broken_xml_are_rejected(body):
    with pytest.raises((ValueError, rss.ElementTree.ParseError)):
        rss.parse_nvidia_feed(body)


def test_size_bound_and_company_blog_links():
    with pytest.raises(ValueError):
        rss.parse_nvidia_feed(b"x" * (rss.MAX_BYTES + 1))
    assert rss.parse_nvidia_feed(feed(url="https://blogs.nvidia.com/blog/controlled"))[0].url.endswith("controlled")


def test_utf16_cannot_bypass_entity_guard_and_utf8_bom_remains_valid():
    document = ('<!DOCTYPE rss [<!ENTITY x "expanded">]><rss><channel><title>NVIDIA</title>'
                '<item><title>&x;</title><link>https://blogs.nvidia.com/blog/controlled</link>'
                '</item></channel></rss>')
    with pytest.raises(ValueError):
        rss.parse_nvidia_feed(document.encode("utf-16"))
    assert rss.parse_nvidia_feed(b"\xef\xbb\xbf" + feed())[0].publisher == "NVIDIA"


@pytest.mark.parametrize("status", [200, 302, 429])
def test_fixed_request_no_redirect_and_actual_attempt(monkeypatch, status):
    calls = []

    class Response:
        status_code = status

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_content(self, **_kwargs):
            yield feed()

    class Session(Response):
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return Response()

    import requests
    monkeypatch.setattr(requests, "Session", Session)
    connection = Connection()
    rss.nvidia_news_worker(connection, rss.NvidiaNewsSettings(), "ignored", 2, 3, time.monotonic() + 5)
    assert len(calls) == 1 and calls[0][0] == rss.FEED_URL
    assert calls[0][1]["allow_redirects"] is False
    assert connection.events.count(("attempt", None)) == 1
    assert connection.closed
    results = [value for kind, value in connection.events if kind == "result"]
    assert results[0]["success"] is (status == 200)
    if status == 302:
        assert ("upstream_failure", None) in connection.events


def test_keyless_settings_use_existing_bounded_process_and_zero_attempt_guards():
    settings = rss.NvidiaNewsSettings()
    value = execute_news_search(
        settings, "NVIDIA", limit=2, days=3, request_budget_ms=5000, worker=controlled_worker,
    )
    assert value.success and value.upstream_attempts == 1 and len(value.records) == 4
    for extras in [{"cursor": "not-supported"}, {"request_budget_ms": 100}]:
        options = {"request_budget_ms": 5000, **extras}
        rejected = execute_news_search(
            settings, "NVIDIA", limit=2, days=3, worker=controlled_worker, **options,
        )
        assert not rejected.success and rejected.upstream_attempts == 0


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "controlled-nvidia-local-test-only")
    monkeypatch.setattr(data_config, "get_data_config", lambda: DataServiceConfig())
    monkeypatch.setattr(source, "nvidia_news_worker", controlled_worker)
    from src.services import thesis_ledger_news_instruments as identities
    monkeypatch.setattr(identities, "_index_rows", lambda: [
        ["NVDA", "NVDA", "NVIDIA", "", "", [], "US", "stock", True],
        ["MSFT", "MSFT", "Microsoft", "", "", [], "US", "stock", True],
    ])
    store = ThesisLedgerControlStore(str(tmp_path / "news.db"))
    return ThesisLedgerProviderRuntime(store), store


def admit(store):
    now = datetime.now(timezone.utc)
    store.record_route_admission_v3(
        key=KEY, target=TARGET, evidence_ref="controlled-nvidia-rss-test", evidence_sha256="a" * 64,
        scope_symbols=["NVDA.US"], scope_date_from=(now - timedelta(days=30)).date().isoformat(),
        scope_date_to=(now + timedelta(days=1)).date().isoformat(), adapter_revision=source.ADAPTER_REVISION,
        source_revision=source.SOURCE_REVISION, credential_revision="not-required",
        valid_from=(now - timedelta(minutes=1)).isoformat(), valid_until=(now + timedelta(hours=1)).isoformat(),
        recorded_by="controlled-test-only",
    )


def apply(store, revision=1):
    store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "rss-policy",
                           "revision": revision, "enabled": True,
                           "routes": [{"key": KEY, "targets": [TARGET], "enabled": True}]})


def test_default_closed_and_formal_admission_partial_metadata_and_revocation(runtime):
    service, store = runtime
    assert source.SOURCE.default_enabled is False
    binding = CAPABILITY_SOURCES.resolve(KEY, TARGET)
    assert binding is not None and binding.admission_required is True
    assert source.manifest()["requiresCredential"] is False
    apply(store)
    assert execute_news_request(service, request()).upstreamAttempts == 0
    store.save_provider_config(source.PROVIDER_ID, {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "rss-enable", "enabled": True,
    })
    assert execute_news_request(service, request()).upstreamAttempts == 0
    admit(store)
    apply(store, 2)
    now = datetime.now(timezone.utc)
    value = execute_news_request(service, request(limit=2, publishedStart=(now - timedelta(days=4)).isoformat()))
    assert value.status == "partial", value.errors
    assert value.upstreamAttempts == 1 and len(value.items) == 2
    assert "issuer_feed_not_exhaustive" in value.coverage.missingReasons and not value.coverage.complete
    assert value.route.providerId == source.PROVIDER_ID
    assert value.route.credentialRevision == "not-required"
    assert all(item.sourceAvailableAt is None and item.excerpt is None for item in value.items)
    assert execute_news_request(service, request(instrument={
        "market": "US", "assetType": "STOCK", "symbol": "MSFT.US"})).upstreamAttempts == 0
    store.revoke_route_admission_v3(key=KEY, target=TARGET, reason="controlled-revoke")
    denied = execute_news_request(service, request())
    assert denied.status == "unavailable" and denied.upstreamAttempts == 0


def test_direct_binding_rejects_other_company_before_execution():
    context = ReadContext(source.NvidiaNewsAdapter(), None, control_epoch=(1, 1), database_path="unused")
    value = source.read(request(instrument={"market": "US", "assetType": "STOCK", "symbol": "MSFT.US"}), context)
    assert value.upstreamAttempts == 0 and value.errors[0].code == "not_covered"
