"""NVIDIA 官方有限 RSS；个人非商业信息用途，不读取文章正文。"""

from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import logging
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree

from src.services.thesis_ledger_news_records import RawNewsRecord

FEED_URL = "https://nvidianews.nvidia.com/releases.xml"
MAX_BYTES = 512 * 1024
MAX_ITEMS = 50
ALLOWED_HOSTS = frozenset({"nvidianews.nvidia.com", "blogs.nvidia.com"})


@dataclass(frozen=True)
class NvidiaNewsSettings:
    provider_id: str = "nvidia-news"
    result_limit: int = MAX_ITEMS

    def configured(self) -> bool:
        return True


def _published_at(value):
    try:
        parsed = parsedate_to_datetime(value or "")
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def parse_nvidia_feed(body: bytes) -> tuple[RawNewsRecord, ...]:
    if len(body) > MAX_BYTES:
        raise ValueError("RSS 响应超限或含不支持的声明")
    text = body.decode("utf-8-sig")
    if "\x00" in text or "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("RSS 含不支持的编码或声明")
    root = ElementTree.fromstring(text)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None or "nvidia" not in (channel.findtext("title") or "").lower():
        raise ValueError("RSS 发布方或结构不匹配")
    result = []
    for item in channel.findall("item")[:MAX_ITEMS]:
        title = " ".join((item.findtext("title") or "").split())
        url = (item.findtext("link") or "").strip()
        parsed = urlsplit(url)
        if (not title or len(title) > 1000 or len(url) > 4096 or parsed.scheme != "https"
                or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            raise ValueError("RSS 条目标题或原文链接无效")
        source_id = (item.findtext("guid") or "").strip()
        result.append(RawNewsRecord(
            title=title, url=url, original_url=url, publisher="NVIDIA",
            source_item_id=source_id if 0 < len(source_id) <= 256 else None,
            published_at=_published_at(item.findtext("pubDate")),
        ))
    return tuple(result)


def _result(connection, *, records=(), success=False):
    connection.send(("result", {"records": records, "success": success, "truncated": True,
                                "examined_count": len(records), "partial": False}))


def nvidia_news_worker(connection, _settings, _query, _max_results, _days, deadline):
    """一次固定 URL 请求；窗口过滤在通用结果层先于条数截取执行。"""
    logging.disable(logging.CRITICAL)
    try:
        import requests
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            connection.send(("timeout", None))
            _result(connection)
            return
        with requests.Session() as session:
            connection.send(("attempt", None))
            with session.get(FEED_URL, timeout=remaining, stream=True, allow_redirects=False,
                             headers={"User-Agent": "ThesisLedger/0.1 RSS reader", "Accept": "application/rss+xml, text/xml"}) as response:
                connection.send(("http_status", response.status_code))
                if response.status_code != 200:
                    if response.status_code < 400:
                        connection.send(("upstream_failure", None))
                    _result(connection)
                    return
                body = bytearray()
                for chunk in response.iter_content(chunk_size=16_384):
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError("RSS 响应超限")
        _result(connection, records=parse_nvidia_feed(bytes(body)), success=True)
    except Exception as error:
        import requests
        code = "timeout" if isinstance(error, requests.exceptions.Timeout) else "upstream_failure"
        connection.send((code, None))
        _result(connection)
    finally:
        connection.close()
