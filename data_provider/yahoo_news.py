"""复用 Yahoo Search.news，仅保留核验关联代码的近期新闻元数据。"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import re

from src.services.thesis_ledger_news_identity import news_url
from src.services.thesis_ledger_news_records import RawNewsRecord

SDK_VERSION = "1.7.0"
SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
MAX_BYTES = 512 * 1024
MAX_ITEMS = 10
ITEM_FIELDS = frozenset({"uuid", "title", "link", "publisher", "providerPublishTime", "relatedTickers"})


class YahooNewsError(ValueError):
    def __init__(self, code="upstream_failure"):
        self.code = code
        super().__init__("Yahoo 新闻未完成有效读取")


@dataclass(frozen=True)
class YahooNewsSettings:
    provider_id: str = "yfinance"
    result_limit: int = MAX_ITEMS

    def configured(self):
        return True


@dataclass(frozen=True)
class YahooNewsPage:
    records: tuple[RawNewsRecord, ...]
    metadata: dict
    http_trace: tuple[dict, ...] = ()


def _published_at(value):
    if type(value) is not int or value < 0:
        return None
    try:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def parse_yahoo_page(body, symbol, *, max_results):
    if (not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}\.US", symbol)):
        raise YahooNewsError()
    ticker = symbol.removesuffix(".US")
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("quotes"), list):
        raise YahooNewsError()
    quotes = payload["quotes"]
    if (len(quotes) != 1 or not isinstance(quotes[0], dict)
            or quotes[0].get("symbol") != ticker or quotes[0].get("quoteType") != "EQUITY"):
        raise YahooNewsError()
    rows = payload.get("news")
    if not isinstance(rows, list) or len(rows) > min(max_results, MAX_ITEMS):
        raise YahooNewsError()
    records, metadata = [], []
    for row in rows:
        if not isinstance(row, dict):
            raise YahooNewsError()
        title, url, identifier = (row.get(k) for k in ("title", "link", "uuid"))
        related = row.get("relatedTickers")
        if (not isinstance(title, str) or not title.strip() or len(title) > 1000 or re.search(r"<[^>]+>", title)
                or not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 256
                or not isinstance(url, str) or not url.startswith("https://") or len(url) > 4096
                or not isinstance(related, list) or ticker not in related
                or any(not isinstance(value, str) for value in related)):
            raise YahooNewsError()
        news_url(url)
        publisher = row.get("publisher")
        records.append(RawNewsRecord(
            title=" ".join(title.split()), url=url, source_item_id=identifier.strip(),
            publisher=publisher.strip() if isinstance(publisher, str) and publisher.strip() else None,
            published_at=_published_at(row.get("providerPublishTime")),
        ))
        metadata.append({key: row.get(key) for key in sorted(ITEM_FIELDS)})
    evidence = {"symbol": symbol, "sdkVersion": SDK_VERSION, "endpoint": SEARCH_URL,
                "responseSha256": sha256(body).hexdigest(), "payload": {
                    "quotes": [{"symbol": ticker, "quoteType": "EQUITY"}], "news": metadata}}
    return YahooNewsPage(tuple(records), evidence)


def fetch_yahoo_news(symbol, connection, deadline, *, max_results):
    import yfinance as yf
    from data_provider.yahoo_news_transport import search_session
    if (yf.__version__ != SDK_VERSION or not 1 <= max_results <= MAX_ITEMS
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}\.US", symbol)):
        raise YahooNewsError("configuration_failure")
    ticker = symbol.removesuffix(".US")
    with search_session(connection, deadline, ticker, max_results) as session:
        result = yf.Search(ticker, max_results=1, news_count=max_results, lists_count=0, recommended=0,
                           include_cb=False, enable_fuzzy_query=False, session=session,
                           timeout=6, raise_errors=True)
        if session.search_body is None or result.news != json.loads(session.search_body).get("news"):
            raise YahooNewsError()
        return replace(parse_yahoo_page(session.search_body, symbol, max_results=max_results),
                       http_trace=tuple(session.trace))


def yahoo_news_worker(connection, _settings, query, max_results, _days, deadline):
    logging.disable(logging.CRITICAL)
    page = None
    try:
        page = fetch_yahoo_news(query, connection, deadline, max_results=max_results)
    except YahooNewsError as error:
        connection.send((error.code, None))
    except Exception as error:
        from curl_cffi import requests
        connection.send(("timeout" if isinstance(error, requests.exceptions.Timeout) else "upstream_failure", None))
    finally:
        connection.send(("result", {"records": page.records if page else (), "success": page is not None,
                                    "truncated": len(page.records) >= max_results if page else False,
                                    "examined_count": len(page.records) if page else 0, "partial": False}))
        connection.close()
