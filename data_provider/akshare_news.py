"""现有 AKShare 个股新闻：有限搜索第一页，仅投影公开元数据。"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import inspect
import logging
import re
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from src.services.thesis_ledger_news_records import RawNewsRecord

SEARCH_URL = "https://search-api-web.eastmoney.com/search/jsonp"
SDK_VERSIONS = frozenset({"1.18.94", "1.19.1"})
SDK_FUNCTION_SHA256 = "5c2edc3f07a0273e05817410f10ba1f2a9ffc4f365aa81449fdb70d9cbe4e3e5"
MAX_BYTES = 512 * 1024
MAX_ITEMS = 10


@dataclass(frozen=True)
class AkshareNewsSettings:
    provider_id: str = "akshare"
    result_limit: int = MAX_ITEMS

    def configured(self):
        return True


def published_at(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        if parsed.strftime("%Y-%m-%d %H:%M:%S") == value:
            return parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai")).isoformat()
    except ValueError:
        pass
    return None


def project_news(frame, symbol):
    required = {"关键词", "新闻标题", "发布时间", "文章来源", "新闻链接"}
    if not required.issubset(frame.columns) or len(frame) > MAX_ITEMS:
        raise ValueError("AKShare 新闻结构或条数变化")
    records = []
    for row in frame.to_dict("records"):
        if str(row["关键词"]) != symbol:
            raise ValueError("AKShare 新闻搜索代码不匹配")
        title, url, publisher = (row[key] for key in ("新闻标题", "新闻链接", "文章来源"))
        if (not isinstance(title, str) or not title.strip() or len(title) > 1000
                or re.search(r"<[^>]+>", title) or not isinstance(url, str) or len(url) > 4096):
            raise ValueError("AKShare 新闻标题或链接无效")
        parsed = urlsplit(url)
        article = re.fullmatch(r"/a/(\d{18,30})\.html", parsed.path)
        if (parsed.scheme not in {"http", "https"} or parsed.hostname != "finance.eastmoney.com"
                or parsed.username or parsed.password or parsed.port is not None
                or parsed.query or parsed.fragment or article is None):
            raise ValueError("AKShare 新闻链接不属于公开转载页")
        records.append(RawNewsRecord(
            title=" ".join(title.split()), url="https://finance.eastmoney.com" + parsed.path,
            publisher=publisher.strip() if isinstance(publisher, str) and publisher.strip() else None,
            source_item_id=article.group(1), published_at=published_at(row["发布时间"]),
        ))
    return tuple(records)


def fetch_news(symbol, connection, deadline):
    """复用 SDK 参数和解析，单独约束其传输；隔离进程内临时替换并恢复。"""
    import akshare as ak
    from akshare.news import news_stock
    from curl_cffi import requests

    if (ak.__version__ not in SDK_VERSIONS or not re.fullmatch(r"\d{6}", symbol)
            or sha256(inspect.getsource(ak.stock_news_em).encode()).hexdigest() != SDK_FUNCTION_SHA256):
        raise ValueError("AKShare 新闻版本或代码未经核验")
    used = False

    def get(url, *, params, **_sdk_options):
        nonlocal used
        if used or url != SEARCH_URL:
            raise ValueError("AKShare 新闻请求目标或次数变化")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.Timeout("新闻总请求预算已耗尽")
        used = True
        with requests.Session() as session:
            connection.send(("attempt", None))
            headers = {name: value for name, value in _sdk_options.get("headers", {}).items()
                       if name.lower() in {"accept", "referer", "user-agent"}}
            headers["referer"] = f"https://so.eastmoney.com/news/s?keyword={symbol}"
            response = session.get(url, params=params, timeout=remaining, stream=True,
                                   allow_redirects=False, headers=headers)
            try:
                connection.send(("http_status", response.status_code))
                if response.status_code != 200:
                    raise requests.exceptions.RequestException("新闻来源未返回成功")
                body = bytearray()
                for chunk in response.iter_content():
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise ValueError("AKShare 新闻响应超限")
            finally:
                response.close()
        return SimpleNamespace(text=body.decode("utf-8"))

    original = news_stock.requests.get
    news_stock.requests.get = get
    try:
        return project_news(ak.stock_news_em(symbol=symbol), symbol)
    finally:
        news_stock.requests.get = original


def akshare_news_worker(connection, _settings, query, _limit, _days, deadline):
    logging.disable(logging.CRITICAL)
    records, success = (), False
    try:
        if not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", query):
            raise ValueError("新闻代码必须是规范 A 股身份")
        records = fetch_news(query.split(".")[0], connection, deadline)
        success = True
    except Exception as error:
        from curl_cffi import requests
        connection.send(("timeout" if isinstance(error, requests.exceptions.Timeout) else "upstream_failure", None))
    finally:
        connection.send(("result", {"records": records, "success": success, "truncated": True,
                                    "examined_count": len(records), "partial": False}))
        connection.close()
