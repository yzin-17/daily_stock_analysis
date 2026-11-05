"""有限公开文档读取：正文变化、重定向、认证隔离、大小与总超时。"""

import asyncio

import httpx
import pytest

from src.services.rule_monitor_fetch import DocumentError, MAX_BYTES, document_digest, fetch_document
from src.services.rule_monitor_registry import allowed_document_url, sources

URL = "https://www.sse.com.cn/controlled-rule"
TEXT = "受控官方收费规则正文，用于验证摘要变化和导航隔离。" * 10


def test_finite_registry_and_canonical_content_ignore_navigation_noise():
    registry = sources()
    assert len(registry) == 9
    assert len({doc["url"] for source in registry.values() for doc in source.documents}) <= 24
    first = document_digest(f"<nav>随机123</nav><main>{TEXT}</main>".encode(), "text/html")
    same = document_digest(f"<nav>随机456</nav><main>{TEXT}</main><script>noise()</script>".encode(), "text/html")
    assert first["rawHash"] != same["rawHash"]
    assert first["contentHash"] == same["contentHash"]
    nested_nav = document_digest(f'<div class="navbar"><div>随机导航</div>更新日期</div><main>{TEXT}</main><aside>其他市场动态</aside>'.encode(), "text/html")
    assert nested_nav["contentHash"] == first["contentHash"]
    linked = document_digest(f'<main>{TEXT}<a href="old.pdf">现行规则附件</a></main>'.encode(), "text/html")
    replaced = document_digest(f'<main>{TEXT}<a href="new.pdf">现行规则附件</a></main>'.encode(), "text/html")
    assert linked["contentHash"] != replaced["contentHash"]
    different = document_digest(f"<main>{TEXT}更新条文</main>".encode(), "text/html")
    assert different["contentHash"] != first["contentHash"]
    gbk = document_digest(f'<meta charset="gb2312"><main>{TEXT}</main>'.encode("gb2312"), "text/html")
    assert gbk["contentHash"] == first["contentHash"]
    for url in ["http://127.0.0.1/private", "https://www.sse.com.cn@127.0.0.1/", "file:///tmp/a"]:
        assert not allowed_document_url(url)


@pytest.mark.parametrize("status,body,content_type,error", [
    (302, b"", "text/html", "document_http_error"),
    (429, b"", "text/html", "document_http_error"),
    (200, b"x" * (MAX_BYTES + 1), "text/html", "document_too_large"),
    (200, ("captcha " + TEXT).encode(), "text/html", "document_invalid"),
    (200, b"<html>challenge</html>", "application/pdf", "document_invalid"),
])
def test_failures_are_single_request_and_not_rule_changes(status, body, content_type, error):
    calls = []
    def handle(request):
        calls.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(status, content=body, headers={"Content-Type": content_type, "Location": URL + "/redirect"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
            with pytest.raises(DocumentError) as failure:
                await fetch_document(URL, client=client)
            assert failure.value.code == error
    asyncio.run(run())
    assert len(calls) == 1


def test_total_budget_cancels_slow_response_and_unknown_url_is_zero_request():
    calls = []
    async def handle(request):
        calls.append(request)
        await asyncio.sleep(0.1)
        return httpx.Response(200, text=TEXT, headers={"Content-Type": "text/html"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(DocumentError, match="document_url_invalid"):
                await fetch_document("https://127.0.0.1/internal", client=client)
            assert not calls
            with pytest.raises(DocumentError, match="document_unavailable"):
                await fetch_document(URL, client=client, budget_seconds=0.001)
    asyncio.run(run())
    assert len(calls) == 1
