"""分页顺序、继续点作用域和有界容量；不访问外部来源。"""

from dataclasses import replace
import json

import pytest

from data_provider.hithink_news_pagination import project_page
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_news_pagination import NewsCursorCache, NewsPageState
from src.services.thesis_ledger_news_records import RawNewsRead, RawNewsRecord


def state(offset="next"):
    return NewsPageState("fixed-scope", "hithink", "timeline", offset)


def raw(*, offset="next", times=("2026-11-09T12:00:00Z",), more=True):
    return RawNewsRead(
        records=tuple(RawNewsRecord(str(i), f"https://news.10jqka.com.cn/{i}", published_at=t)
                      for i, t in enumerate(times)),
        success=True, upstream_attempts=1, truncated=more, next_cursor=offset, examined_count=len(times),
    )


def test_cursor_is_opaque_scope_bound_repeatable_and_expires():
    cache = NewsCursorCache(clock=lambda: 10, ttl=30)
    token = cache.put(state())
    assert "fixed-scope" not in token and "hithink" not in token and len(token) < 2048
    assert cache.get(token, "fixed-scope") == cache.get(token, "fixed-scope") == state()
    with pytest.raises(CapabilityContractError, match="条件或控制版本"):
        cache.get(token, "changed-scope")
    cache.clock = lambda: 41
    with pytest.raises(CapabilityContractError, match="已失效"):
        cache.get(token)


def test_cursor_capacity_and_byte_budget_evict_oldest():
    cache = NewsCursorCache(capacity=1)
    first = cache.put(state("one"))
    second = cache.put(state("two"))
    with pytest.raises(CapabilityContractError):
        cache.get(first)
    assert cache.get(second).offset == "two"
    with pytest.raises(CapabilityContractError, match="容量"):
        NewsCursorCache(max_bytes=10).put(state())


def test_pages_keep_empty_continuation_and_terminate_only_verified_window_boundary():
    page = project_page(raw(times=("2020-01-01T00:00:00Z",)), None,
                        scope="fixed-scope", provider="hithink", source="timeline")
    assert page.next_cursor and page.truncated
    bounded = project_page(raw(times=("2020-01-01T00:00:00Z",)), None,
                           scope="fixed-scope", provider="hithink", source="timeline",
                           published_start="2026-11-06T00:00:00Z")
    assert bounded.next_cursor is None and not bounded.truncated
    unknown = project_page(raw(times=(None, "2020-01-01T00:00:00Z")), None,
                           scope="fixed-scope", provider="hithink", source="timeline",
                           published_start="2026-11-06T00:00:00Z")
    assert unknown.next_cursor and "published_time_unknown" in unknown.missing_reasons
    reversed_page = project_page(raw(times=("2020-01-01T00:00:00Z", "2026-11-07T00:00:00Z")), None,
                                scope="fixed-scope", provider="hithink", source="timeline",
                                published_start="2026-11-06T00:00:00Z")
    assert reversed_page.next_cursor and "timeline_order_unknown" in reversed_page.missing_reasons
    empty = project_page(raw(times=()), None, scope="fixed-scope", provider="hithink", source="timeline")
    assert empty.next_cursor and empty.records == ()
    final = project_page(raw(offset=None, more=False), None,
                         scope="fixed-scope", provider="hithink", source="timeline")
    assert not final.truncated and final.next_cursor is None and final.missing_reasons == ()


def test_repeated_or_missing_offsets_fail_instead_of_claiming_exhaustion():
    for value in [raw(offset="first"), raw(offset=None)]:
        with pytest.raises(CapabilityContractError, match="未前进"):
            project_page(value, None, scope="fixed-scope", provider="hithink", source="timeline")
    with pytest.raises(CapabilityContractError):
        project_page(raw(offset="old"), replace(state(), seen_offsets=("old",)),
                     scope="fixed-scope", provider="hithink", source="timeline")


def test_unknown_time_and_cross_page_order_defects_survive_to_last_page():
    previous = replace(state(), missing_reasons=("published_time_unknown",),
                       last_published="2026-11-08T00:00:00Z")
    final = project_page(raw(more=False), previous,
                         scope="fixed-scope", provider="hithink", source="timeline")
    assert set(final.missing_reasons) == {"published_time_unknown", "timeline_order_unknown"}


def test_transport_sends_the_exact_offset_without_extra_row_loss(monkeypatch):
    from data_provider import hithink_news as news
    calls = []
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_content(self, **_):
            yield json.dumps({"code": 0, "data": {"items": [], "has_more": False, "offset": None}}).encode()
    class Session(Response):
        def get(self, url, **kwargs):
            calls.append(kwargs["params"])
            return Response()
    import requests
    import time
    monkeypatch.setattr(requests, "Session", Session)
    class Connection:
        def send(self, event): pass
    news.fetch_hithink_news(news.HithinkNewsSettings("controlled-key"), "600036.SH",
                            Connection(), time.monotonic() + 5, max_results=5, offset="literal+offset/==")
    assert calls == [{"thscode": "600036.SH", "size": 5, "offset": "literal+offset/=="}]
