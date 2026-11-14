"""HiThink 个股时间线的单页读取：原样消费来源 offset，只投影元数据。"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import re
import time
from urllib.parse import urlsplit

from src.services.thesis_ledger_news_records import RawNewsRecord

NEWS_URL = "https://fuyao.aicubes.cn/api/news/stock/timeline-list"
MAX_BYTES = 512 * 1024
MAX_ITEMS = 100


@dataclass(frozen=True)
class HithinkNewsSettings:
    api_key: str = field(repr=False)
    provider_id: str = "hithink"
    result_limit: int = MAX_ITEMS
    paginated: bool = True

    def configured(self):
        return bool(self.api_key.strip())


@dataclass(frozen=True)
class HithinkNewsPage:
    records: tuple[RawNewsRecord, ...]
    has_more: bool
    offset: str | None
    response_sha256: str


class HithinkNewsError(ValueError):
    def __init__(self, code="upstream_failure"):
        self.code = code
        super().__init__("HiThink 新闻未完成有效读取")


def _published_at(value):
    if type(value) is not int or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def parse_hithink_page(body, *, max_results):
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
        raise HithinkNewsError()
    payload = json.loads(body)
    code = payload.get("code") if isinstance(payload, dict) else None
    if type(code) is not int or code != 0:
        raise HithinkNewsError("rate_limited" if type(code) is int and code == 4001 else "upstream_failure")
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise HithinkNewsError()
    items, more, offset = data["items"], data.get("has_more"), data.get("offset")
    if (len(items) > min(max_results, MAX_ITEMS) or type(more) is not bool
            or more and (not isinstance(offset, str) or not offset or len(offset) > 4096)):
        raise HithinkNewsError()
    records = []
    for row in items:
        if not isinstance(row, dict):
            raise HithinkNewsError()
        title, url, identifier = (row.get(k) for k in ("title", "jump_url", "id"))
        if (not isinstance(title, str) or not title.strip() or len(title) > 1000 or re.search(r"<[^>]+>", title)
                or not isinstance(url, str) or len(url) > 4096
                or not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256):
            raise HithinkNewsError()
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if (parsed.scheme not in {"https", "http"} or not host.endswith(".10jqka.com.cn")
                or parsed.username or parsed.password or parsed.port is not None):
            raise HithinkNewsError()
        publisher = row.get("source")
        records.append(RawNewsRecord(
            title=" ".join(title.split()), url=url, source_item_id=identifier.strip(),
            publisher=publisher.strip() if isinstance(publisher, str) and publisher.strip() else None,
            published_at=_published_at(row.get("publish_time_ms")),
        ))
    return HithinkNewsPage(tuple(records), more, offset if isinstance(offset, str) else None, sha256(body).hexdigest())


def fetch_hithink_news(settings, symbol, connection, deadline, *, max_results, offset="first"):
    import requests
    if (not settings.configured() or not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", symbol)
            or not isinstance(offset, str) or not 0 < len(offset) <= 4096):
        raise HithinkNewsError("configuration_failure")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise requests.Timeout("新闻总请求预算已耗尽")
    with requests.Session() as session:
        connection.send(("attempt", None))
        with session.get(NEWS_URL, params={"thscode": symbol, "offset": offset, "size": max_results},
                         headers={"X-api-key": settings.api_key}, timeout=remaining,
                         stream=True, allow_redirects=False) as response:
            connection.send(("http_status", response.status_code))
            if response.status_code != 200:
                raise HithinkNewsError()
            body = bytearray()
            for chunk in response.iter_content(chunk_size=16_384):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise HithinkNewsError()
    return parse_hithink_page(bytes(body), max_results=max_results)


def hithink_news_worker(connection, settings, query, max_results, _days, deadline):
    logging.disable(logging.CRITICAL)
    page = None
    try:
        symbol, offset = query if isinstance(query, tuple) else (query, "first")
        page = fetch_hithink_news(settings, symbol, connection, deadline, max_results=max_results, offset=offset)
    except HithinkNewsError as error:
        connection.send((error.code, None))
    except Exception as error:
        import requests
        connection.send(("timeout" if isinstance(error, requests.exceptions.Timeout) else "upstream_failure", None))
    finally:
        connection.send(("result", {"records": page.records if page else (), "success": page is not None,
                                    "truncated": page.has_more if page else False,
                                    "next_cursor": page.offset if page and page.has_more else None,
                                    "examined_count": len(page.records) if page else 0, "partial": False}))
        connection.close()
