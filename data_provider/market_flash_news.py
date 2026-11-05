"""复用 a-stock-data 的财联社签名与见闻频道，仅取有限快讯元数据。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import md5, sha1, sha256
import json
import logging
import re
import time
from urllib.parse import urlsplit

from src.services.thesis_ledger_news_identity import news_url
from src.services.thesis_ledger_news_records import RawNewsRecord

# Apache-2.0: simonlin1212/a-stock-data, SKILL.md L3557-L3698.
UPSTREAM_COMMIT = "29b07d2e37005b9fd568bca1262f6e572b5f9407"
CLS_URL = "https://www.cls.cn/v1/roll/get_roll_list"
WSCN_URL = "https://api-one-wscn.awtmt.com/apiv1/content/lives"
MAX_BYTES = 512 * 1024
FIELDS = {
    "cls": frozenset({"id", "title", "brief", "shareurl", "ctime"}),
    "wallstreetcn": frozenset({"id", "title", "content_text", "uri", "display_time"}),
}


class FlashNewsError(ValueError):
    def __init__(self, code="upstream_failure"):
        self.code = code
        super().__init__("市场快讯未完成有效读取")


@dataclass(frozen=True)
class FlashNewsSettings:
    provider_id: str

    @property
    def result_limit(self):
        return 5 if self.provider_id == "cls" else 20

    def configured(self):
        return self.provider_id in FIELDS


@dataclass(frozen=True)
class FlashNewsPage:
    records: tuple[RawNewsRecord, ...]
    metadata: dict
    missing_reasons: tuple[str, ...] = ()


def flash_request(provider, max_results):
    if provider not in FIELDS or type(max_results) is not int or not 1 <= max_results <= FlashNewsSettings(provider).result_limit:
        raise FlashNewsError("configuration_failure")
    if provider == "cls":
        params = {"appName": "CailianpressWeb", "os": "web", "sv": "7.7.5", "last_time": "",
                  "refresh_type": "1", "rn": str(max_results)}
        query = "&".join(f"{key}={params[key]}" for key in sorted(params))
        sign = md5(sha1(query.encode()).hexdigest().encode()).hexdigest()
        return CLS_URL + "?" + query + "&sign=" + sign, {}, "https://www.cls.cn/"
    return WSCN_URL, {"channel": "a-stock-channel", "limit": max_results}, "https://wallstreetcn.com/"


def _record(row, provider):
    if not isinstance(row, dict):
        raise FlashNewsError()
    identifier = row.get("id")
    if type(identifier) is int and identifier > 0:
        identifier = str(identifier)
    if not isinstance(identifier, str) or not re.fullmatch(r"[1-9]\d{0,30}", identifier):
        raise FlashNewsError()
    title = row.get("title")
    if title is not None and not isinstance(title, str):
        raise FlashNewsError()
    projected = not title or not title.strip()
    if projected:
        title = row.get("brief" if provider == "cls" else "content_text")
    if not isinstance(title, str) or not title.strip() or re.search(r"<[^>]+>", title):
        raise FlashNewsError()
    title = " ".join(title.split())
    if projected:
        title = title[:200]
    if len(title) > 1000:
        raise FlashNewsError()
    url = row.get("shareurl" if provider == "cls" else "uri")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise FlashNewsError()
    news_url(url)
    parsed = urlsplit(url)
    allowed_hosts = {"www.cls.cn", "cls.cn", "api3.cls.cn"} if provider == "cls" else {"wallstreetcn.com", "www.wallstreetcn.com"}
    if parsed.hostname not in allowed_hosts or not parsed.path.rstrip("/").endswith("/" + identifier):
        raise FlashNewsError()
    stamp = row.get("ctime" if provider == "cls" else "display_time")
    published = None
    if type(stamp) is int and stamp > 0:
        try:
            published = datetime.fromtimestamp(stamp, timezone.utc).isoformat()
        except (ValueError, OverflowError, OSError):
            pass
    return RawNewsRecord(title=title, url=url, source_item_id=identifier,
                         publisher="财联社" if provider == "cls" else "华尔街见闻",
                         published_at=published), projected


def parse_flash_page(body, provider, *, max_results):
    flash_request(provider, max_results)
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
        raise FlashNewsError()
    payload = json.loads(body)
    expected_code = 0 if provider == "cls" else 20000
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise FlashNewsError()
    if ((provider != "cls" or "code" in payload)
            and (type(payload.get("code")) is not int or payload["code"] != expected_code)):
        raise FlashNewsError()
    key = "roll_data" if provider == "cls" else "items"
    rows = payload["data"].get(key)
    if not isinstance(rows, list) or len(rows) > max_results:
        raise FlashNewsError()
    records, metadata, reasons = [], [], set()
    for row in rows:
        record, projected = _record(row, provider)
        records.append(record)
        if projected:
            reasons.add("provider_text_used_as_title")
        projected_row = {name: row.get(name) for name in sorted(FIELDS[provider])}
        text_field = "brief" if provider == "cls" else "content_text"
        projected_row[text_field] = record.title if projected else None
        metadata.append(projected_row)
    projected_payload = {"data": {key: metadata}}
    if "code" in payload:
        projected_payload["code"] = payload["code"]
    evidence = {"scope": "CN.STOCK", "upstreamCommit": UPSTREAM_COMMIT,
                "endpoint": CLS_URL if provider == "cls" else WSCN_URL,
                "responseSha256": sha256(body).hexdigest(),
                "payload": projected_payload}
    return FlashNewsPage(tuple(records), evidence, tuple(sorted(reasons)))


def fetch_flash_news(settings, connection, deadline, *, max_results, session_factory=None):
    from curl_cffi import requests
    url, params, referer = flash_request(settings.provider_id, max_results)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise FlashNewsError("timeout")
    factory = session_factory or requests.Session
    with factory(impersonate="chrome") as session:
        connection.send(("attempt", None))
        response = session.get(url, params=params, headers={"Referer": referer},
                               timeout=min(6, remaining), allow_redirects=False, stream=True)
        try:
            connection.send(("http_status", response.status_code))
            if response.status_code != 200:
                raise FlashNewsError("rate_limited" if response.status_code == 429 else "upstream_failure")
            chunks, size = [], 0
            for chunk in response.iter_content():
                if time.monotonic() >= deadline:
                    raise FlashNewsError("timeout")
                size += len(chunk)
                if size > MAX_BYTES:
                    raise FlashNewsError()
                chunks.append(chunk)
            return parse_flash_page(b"".join(chunks), settings.provider_id, max_results=max_results)
        finally:
            response.close()


def flash_news_worker(connection, settings, _query, max_results, _days, deadline):
    logging.disable(logging.CRITICAL)
    page = None
    try:
        page = fetch_flash_news(settings, connection, deadline, max_results=max_results)
    except FlashNewsError as error:
        connection.send((error.code, None))
    except Exception as error:
        from curl_cffi import requests
        connection.send(("timeout" if isinstance(error, requests.exceptions.Timeout) else "upstream_failure", None))
    finally:
        connection.send(("result", {"records": page.records if page else (), "success": page is not None,
                                    "truncated": len(page.records) >= max_results if page else False,
                                    "examined_count": len(page.records) if page else 0, "partial": False,
                                    "missing_reasons": page.missing_reasons if page else ()}))
        connection.close()
