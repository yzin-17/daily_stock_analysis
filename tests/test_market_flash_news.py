"""市场快讯原文边界和有限传输，不访问真实上游。"""

from datetime import datetime
import json
import time
from types import SimpleNamespace
import pytest
from data_provider import market_flash_news as news


def raw(provider, **changes):
    row = {"id": 123, "title": "公开快讯", "ctime": 1792672493,
           "shareurl": "https://api3.cls.cn/share/article/123?os=web", "brief": "来源快讯文本"}
    key, code = "roll_data", {}
    if provider == "wallstreetcn":
        row = {"id": 123, "title": "", "content_text": "来源已有的市场快讯文本", "display_time": 1792672493,
               "uri": "https://wallstreetcn.com/livenews/123"}
        key, code = "items", {"code": 20000}
    return json.dumps({**code, "data": {key: [{**row, **changes}]}}, ensure_ascii=False).encode()


@pytest.mark.parametrize("provider", ["cls", "wallstreetcn"])
def test_metadata_preserves_original_identity_times_and_unknown_availability(provider):
    page = news.parse_flash_page(raw(provider), provider, max_results=5)
    row, = page.records
    assert row.source_item_id == "123" and datetime.fromisoformat(row.published_at).timestamp() == 1792672493
    assert row.excerpt is row.source_available_at is row.original_url is None
    key = "roll_data" if provider == "cls" else "items"
    assert set(page.metadata["payload"]["data"][key][0]) == news.FIELDS[provider]
    assert ("provider_text_used_as_title" in page.missing_reasons) == (provider == "wallstreetcn")


@pytest.mark.parametrize("provider", ["cls", "wallstreetcn"])
@pytest.mark.parametrize("change", [{"id": True}, {"title": "<script>x</script>"}, {"title": 3},
                                  {"id": "999"}, {"id": ""}])
def test_bad_identity_and_markup_fail_closed(provider, change):
    with pytest.raises(ValueError): news.parse_flash_page(raw(provider, **change), provider, max_results=5)


@pytest.mark.parametrize("provider,field", [("cls", "shareurl"), ("wallstreetcn", "uri")])
@pytest.mark.parametrize("url", ["http://api3.cls.cn/share/article/123", "https://bad.test/123",
                                "https://secret:token@api3.cls.cn/share/article/123"])
def test_source_links_must_be_public_and_match_real_publisher(provider, field, url):
    with pytest.raises(ValueError): news.parse_flash_page(raw(provider, **{field: url}), provider, max_results=5)


@pytest.mark.parametrize("value", [None, True, "1792672493", -1, 1792672493000])
def test_unknown_time_is_not_replaced_by_fetch_time(value):
    row, = news.parse_flash_page(raw("cls", ctime=value), "cls", max_results=5).records
    assert row.published_at is None


def test_sorted_signature_and_channel_are_fixed_and_bad_settings_have_no_network():
    url, params, referer = news.flash_request("cls", 5)
    query = url.split("?", 1)[1].split("&sign=", 1)[0]
    assert query.split("&") == sorted(query.split("&")) and params == {} and referer == "https://www.cls.cn/"
    assert news.flash_request("wallstreetcn", 6)[1] == {"channel": "a-stock-channel", "limit": 6}
    for provider, count in [("other", 5), ("cls", 6), ("wallstreetcn", 101), ("cls", True)]:
        with pytest.raises(news.FlashNewsError): news.flash_request(provider, count)


def test_empty_page_is_distinct_from_failed_payload_overflow_and_false_success_code():
    assert news.parse_flash_page(b'{"data":{"roll_data":[]}}', "cls", max_results=5).records == ()
    assert news.parse_flash_page(b'{"code":20000,"data":{"items":[]}}', "wallstreetcn", max_results=5).records == ()
    for provider, body in [("cls", b'{"code":false,"data":{"roll_data":[]}}'),
                           ("wallstreetcn", b'{"code":0,"data":{"items":[]}}'),
                           ("cls", b'{}'), ("cls", b'x' * (news.MAX_BYTES + 1)), ("cls", b'[]')]:
        with pytest.raises(ValueError): news.parse_flash_page(body, provider, max_results=5)


@pytest.mark.parametrize("status,code", [(200, None), (302, "upstream_failure"), (429, "rate_limited")])
def test_http_no_redirect_retry_or_body_fetch_and_response_is_closed(status, code):
    events, calls, closed = [], [], []
    class Session:
        def __init__(self, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(status_code=status, iter_content=lambda: iter([raw("cls")]),
                                   close=lambda: closed.append(True))
    connection = SimpleNamespace(send=events.append)
    if code:
        with pytest.raises(news.FlashNewsError) as error:
            news.fetch_flash_news(news.FlashNewsSettings("cls"), connection, time.monotonic()+3,
                                  max_results=5, session_factory=Session)
        assert error.value.code == code
    else:
        assert len(news.fetch_flash_news(news.FlashNewsSettings("cls"), connection, time.monotonic()+3,
                                        max_results=5, session_factory=Session).records) == 1
    assert len(calls) == len(closed) == 1 and events == [("attempt", None), ("http_status", status)]
    assert calls[0][1]["allow_redirects"] is False and calls[0][1]["stream"] is True
    assert 0 < calls[0][1]["timeout"] <= 3
