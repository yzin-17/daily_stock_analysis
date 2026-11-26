"""公开法律资料的单次有限读取与正文摘要，不携带业务认证。"""

from hashlib import sha256
from html.parser import HTMLParser
from contextlib import AsyncExitStack
from email.message import Message
import asyncio
import io
import json
import re
import zipfile
from xml.etree import ElementTree
from urllib.parse import urlsplit

import httpx

from src.services.rule_monitor_registry import allowed_document_url
from src.services.rule_monitor_snapshot import pdf_snapshot, readable_text, text_snapshot

MAX_BYTES = 2 * 1024 * 1024
BLOCKS = {"p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "li", "tr", "blockquote"}


class DocumentError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.skip_depth = 0
        self.primary_depth = 0
        self.parts = []
        self.primary = []
        self.links = []
        self.primary_links = []

    def handle_starttag(self, tag, attrs):
        if tag in BLOCKS or tag == "br":
            self.handle_data("\n")
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        values = dict(attrs)
        href = values.get("href")
        if tag == "a" and href and not self.skip_depth:
            parsed = urlsplit(href)
            if parsed.scheme in {"", "http", "https"} and parsed.path:
                reference = href.split("#", 1)[0]
                self.links.append(reference)
                if self.primary_depth:
                    self.primary_links.append(reference)
        tokens = set((values.get("class") or "").lower().split())
        element_id = (values.get("id") or "").lower()
        skip = (tag in {"script", "style", "nav", "header", "footer", "noscript"}
            or values.get("role") in {"navigation", "banner", "contentinfo"}
            or bool(tokens & {"nav", "navbar", "navigation", "footer", "header", "sidebar", "breadcrumb"}))
        primary = (tag in {"main", "article"} or values.get("role") == "main"
            or element_id in {"zoom", "article", "article-content", "main-content"}
            or bool(tokens & {"article-content", "news-content", "content-main"}))
        self.stack.append((tag, skip, primary))
        self.skip_depth += int(skip)
        self.primary_depth += int(primary)

    def handle_endtag(self, tag):
        if tag in BLOCKS:
            self.handle_data("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                removed = self.stack[index:]
                del self.stack[index:]
                self.skip_depth -= sum(int(item[1]) for item in removed)
                self.primary_depth -= sum(int(item[2]) for item in removed)
                return

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_data(self, data):
        if not self.skip_depth:
            self.parts.append(data)
            if self.primary_depth:
                self.primary.append(data)


def document_digest(body, content_type):
    raw_hash = sha256(body).hexdigest()
    if body.startswith(b"%PDF-"):
        if len(body) < 100:
            raise DocumentError("document_invalid")
        return {"kind": "pdf", "rawHash": raw_hash, "contentHash": raw_hash,
            "snapshot": pdf_snapshot(body)}
    if body.startswith(b"PK"):
        try:
            archive = zipfile.ZipFile(io.BytesIO(body))
            if sum(item.file_size for item in archive.infolist()) > 8 * MAX_BYTES:
                raise DocumentError("document_too_large")
            tree = ElementTree.fromstring(archive.read("word/document.xml"))
            text = " ".join(tree.itertext())
            snapshot_text = readable_text("\n".join(" ".join(part.itertext()) for part in tree.iter()
                if part.tag.endswith("}p")))
        except (KeyError, ValueError, zipfile.BadZipFile, ElementTree.ParseError) as error:
            raise DocumentError("document_invalid") from error
        kind = "docx"
    elif "html" in content_type.lower():
        parser = _Text()
        header = Message()
        header["content-type"] = content_type
        match = re.search(br'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)', body[:8192])
        encoding = header.get_content_charset() or (match[1].decode("ascii") if match else "utf-8")
        try:
            parser.feed(body.decode(encoding))
        except (UnicodeDecodeError, LookupError) as error:
            raise DocumentError("document_invalid") from error
        text = " ".join(parser.primary or parser.parts)
        links = parser.primary_links if parser.primary else parser.links
        references = json.dumps(sorted(set(links)), ensure_ascii=False, separators=(",", ":"))
        kind = "html"
    else:
        raise DocumentError("document_invalid")
    if kind == "html":
        snapshot_text = readable_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 80 or any(term in text.lower() for term in
            ("access denied", "captcha", "verify you are human", "request unsuccessful", "验证码",
             "just a moment", "enable javascript and cookies", "安全验证", "checking your browser")):
        raise DocumentError("document_invalid")
    content = kind + ":" + text
    if kind == "html":
        content += "\n引用:" + references
        if links:
            snapshot_text += "\n\n引用链接：\n" + "\n".join(sorted(set(links)))
    return {"kind": kind, "rawHash": raw_hash, "contentHash": sha256(content.encode()).hexdigest(),
        "snapshot": text_snapshot(kind, snapshot_text)}


async def fetch_document(url, *, client=None, budget_seconds=15):
    if not allowed_document_url(url):
        raise DocumentError("document_url_invalid")
    try:
        async with asyncio.timeout(max(0.001, min(15, budget_seconds))), AsyncExitStack() as resources:
            reader = client or await resources.enter_async_context(httpx.AsyncClient())
            async with reader.stream("GET", url, timeout=httpx.Timeout(10, connect=5), follow_redirects=False,
                    headers={"User-Agent": "ThesisLedger-rule-monitor/0.1"}) as response:
                if response.status_code != 200:
                    raise DocumentError("document_http_error")
                body = bytearray()
                async for chunk in response.aiter_bytes(64 * 1024):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise DocumentError("document_too_large")
                return await asyncio.to_thread(document_digest, bytes(body), response.headers.get("Content-Type", ""))
    except (httpx.HTTPError, TimeoutError) as error:
        raise DocumentError("document_unavailable") from error
