"""有限公开文档读取：正文变化、重定向、认证隔离、大小与总超时。"""

import asyncio

import httpcore
import httpx
import pytest

from src.services.rule_monitor_fetch import DocumentError, MAX_BYTES, document_digest, fetch_document
from src.services.rule_monitor_registry import PACKS, allowed_document_url, sources
from tests.test_rule_monitor_official_text import HKEX_HTML, SEC_HTML, SFC_PDF
from tests.test_rule_monitor_snapshots import pdf_bytes

URL = "https://www.sse.com.cn/controlled-rule"
RULE31_HTML = "https://www.govinfo.gov/content/pkg/FR-2004-07-07/html/04-15081.htm"
FINRA_HTML = "https://www.govinfo.gov/content/pkg/FR-2020-10-20/html/2020-23141.htm"
STAMP_URL = "https://www.hkex.com.hk/-/media/HKEX-Market/Services/Circulars-and-Notices/Participant-and-Members-Circulars/SEHK/2023/ce_SEHK_CT_158_2023.pdf"
FAQ_URL = "https://www.finra.org/rules-guidance/guidance/faqs/trading-activity-fee"
READ_URLS = [URL, RULE31_HTML, FINRA_HTML, HKEX_HTML, SEC_HTML, STAMP_URL, FAQ_URL, SFC_PDF]
TEXT = "受控官方收费规则正文，用于验证摘要变化和导航隔离。" * 10


def test_finite_registry_and_canonical_content_ignore_navigation_noise():
    registry = sources()
    assert set(PACKS) <= set(registry)
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
@pytest.mark.parametrize("url", READ_URLS)
def test_failures_are_single_request_and_not_rule_changes(status, body, content_type, error, url):
    calls = []
    def handle(request):
        calls.append(request)
        assert "authorization" not in request.headers
        return httpx.Response(status, content=body, headers={"Content-Type": content_type, "Location": URL + "/redirect"})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as client:
            with pytest.raises(DocumentError) as failure:
                await fetch_document(url, client=client)
            assert failure.value.code == error
    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize("url", READ_URLS)
def test_total_budget_cancels_slow_response_and_unknown_url_is_zero_request(url):
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
                await fetch_document(url, client=client, budget_seconds=0.001)
    asyncio.run(run())
    assert len(calls) == 1


class _DelayedStream(httpcore.AsyncMockStream):
    def __init__(self, backend):
        super().__init__(list(backend._buffer))
        self.backend = backend

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        try:
            await self.backend.wait("tls", timeout)
            return await super().start_tls(ssl_context, server_hostname, timeout)
        except BaseException:
            await self.aclose()  # 模拟网络后端对握手失败或取消的底层流清理。
            raise

    async def read(self, max_bytes, timeout=None):
        await self.backend.wait("read", timeout)
        return await super().read(max_bytes, timeout)


class _DelayedNetwork(httpcore.AsyncMockBackend):
    """缩放墙钟时间，保留HTTPX/httpcore实际传入的分阶段时限及异常转换。"""

    def __init__(self, phase, delay, body, content_type):
        response = (f"HTTP/1.1 200 OK\r\nContent-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
        super().__init__([response])
        self.phase, self.delay = phase, delay
        self.calls, self.streams, self.timeouts = 0, [], {}

    async def wait(self, phase, timeout):
        self.timeouts[phase] = timeout
        if phase != self.phase:
            return
        assert timeout is not None, "网络阶段必须有时限"
        try:
            async with asyncio.timeout(timeout * 0.1):
                await asyncio.sleep(self.delay * 0.1)
        except TimeoutError as error:
            failure = httpcore.ReadTimeout if phase == "read" else httpcore.ConnectTimeout
            raise failure("受控网络阶段超时") from error

    async def connect_tcp(self, host, port, timeout=None, **kwargs):
        self.calls += 1
        await self.wait("tcp", timeout)
        stream = _DelayedStream(self)
        self.streams.append(stream)
        return stream


@pytest.mark.parametrize("url", [STAMP_URL, FAQ_URL])
@pytest.mark.parametrize("phase,delay,error", [
    ("tcp", 3.4, None), ("tls", 3.4, None),
    ("tcp", 5.2, httpx.ConnectTimeout), ("tls", 5.2, httpx.ConnectTimeout),
    ("read", 10.2, httpx.ReadTimeout),
])
def test_connection_margin_and_phase_limits_through_http_transport(url, phase, delay, error):
    body = f"<main>Q100.22：{TEXT}</main>".encode()
    content_type = "text/html"
    if url == STAMP_URL:
        body, content_type = pdf_bytes(text=True), "application/pdf"
    backend = _DelayedNetwork(phase, delay, body, content_type)

    async def run():
        transport = httpx.AsyncHTTPTransport(retries=0)
        transport._pool._network_backend = backend
        async with httpx.AsyncClient(transport=transport) as client:
            if error:
                with pytest.raises(DocumentError, match="document_unavailable") as failure:
                    await fetch_document(url, client=client)
                assert isinstance(failure.value.__cause__, error)
            else:
                result = await fetch_document(url, client=client)
                assert result["snapshot"]["text"] and not result["snapshot"]["reason"]
    asyncio.run(run())
    assert backend.calls == 1  # TCP或TLS失败后不会自动重试。
    assert all(stream._closed for stream in backend.streams)


@pytest.mark.parametrize("url", [STAMP_URL, FAQ_URL])
@pytest.mark.parametrize("phase", ["tcp", "tls"])
def test_remaining_document_budget_interrupts_connection_and_closes_stream(url, phase):
    backend = _DelayedNetwork(phase, 3.4, b"", "text/html")

    async def run():
        transport = httpx.AsyncHTTPTransport(retries=0)
        transport._pool._network_backend = backend
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(DocumentError, match="document_unavailable") as failure:
                await fetch_document(url, client=client, budget_seconds=0.02)
            assert isinstance(failure.value.__cause__, TimeoutError)
    asyncio.run(run())
    assert backend.calls == 1
    assert all(stream._closed for stream in backend.streams)
