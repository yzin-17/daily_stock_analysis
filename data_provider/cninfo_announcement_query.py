"""巨潮证券机构定位与证券限定的分页公告查询。"""
import re
import requests
import time
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

QUERY_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.cninfo.com.cn/"}
KEYWORDS = ("停牌", "复牌", "风险警示")


def query_announcements(client, symbols, start, end, max_pages, deadline=float("inf"), *, root=None, force=False, validate=None):
    items, errors, truncated = [], [], False
    for symbol in symbols:
        path = None
        if root is not None:
            identity = sha256(f"company-v1:{symbol}:{start}:{end}".encode()).hexdigest()
            path = Path(root) / ".queries" / f"{identity}.json"
            if path.exists() and not force:
                try:
                    cached = json.loads(path.read_text())
                    if validate:
                        for row in cached["items"]:
                            validate(row, symbol)
                    recent = date.fromisoformat(end) >= datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=7)
                    if not recent or time.time() - cached["finishedAt"] < 21600:
                        items.extend((symbol, row) for row in cached["items"])
                        continue
                except (ValueError, KeyError, TypeError, OSError):
                    pass
        rows, failed, cut = _query_announcements(client, [symbol], start, end, max_pages, deadline)
        if validate:
            try:
                for row_symbol, row in rows:
                    validate(row, row_symbol)
            except (ValueError, KeyError, TypeError) as exc:
                failed.append({"stage": "query", "symbol": symbol, "reason": str(exc)})
        items.extend(rows)
        errors.extend(failed)
        truncated = truncated or cut
        if path is not None and not failed and not cut:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump({"finishedAt": time.time(), "items": [row for _, row in rows]}, stream, ensure_ascii=False)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
    return items, errors, truncated


def _query_announcements(client, symbols, start, end, max_pages, deadline):
    items, errors, truncated = [], [], False
    for symbol in symbols:
        code = symbol.split(".")[0]
        try:
            response = client.post(SEARCH_URL, data={"keyWord": code, "maxNum": 10}, headers=HEADERS, timeout=20)
            response.raise_for_status()
            matches = [row for row in response.json() if row.get("code") == code and row.get("category") == "A股"]
            if len(matches) != 1 or not re.fullmatch(r"[A-Za-z0-9]+", matches[0].get("orgId", "")):
                raise ValueError("未找到唯一证券机构标识")
            stock = f"{code},{matches[0]['orgId']}"
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            errors.append({"stage": "query", "symbol": symbol, "reason": str(exc)})
            continue
        for keyword in KEYWORDS:
            for page in range(1, max_pages + 1):
                try:
                    if time.monotonic() >= deadline:
                        raise ValueError("公告获取达到本次时间上限")
                    response = client.post(QUERY_URL, data={
                        "pageNum": page, "pageSize": 30, "tabName": "fulltext", "column": "szse",
                        "stock": stock, "searchkey": keyword, "seDate": f"{start}~{end}", "isHLtitle": "true",
                    }, headers=HEADERS, timeout=20)
                    response.raise_for_status()
                    result = response.json()
                    rows = result.get("announcements")
                    if rows is None and result.get("totalAnnouncement") == 0:
                        rows = []
                    if not isinstance(rows, list) or type(result.get("hasMore")) is not bool:
                        raise ValueError("公告检索响应无效")
                    if any(row.get("secCode") != code for row in rows):
                        raise ValueError("精确公告检索返回其他证券")
                    items.extend((symbol, row) for row in rows)
                    if not result["hasMore"]:
                        break
                    if page == max_pages:
                        truncated = True
                except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                    errors.append({"stage": "query", "symbol": symbol, "keyword": keyword, "page": page, "reason": str(exc)})
                    break
    return items, errors, truncated
