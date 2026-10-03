"""有界读取东财基金分红页面，仅解析 JSON 字面量，不执行远端脚本。"""

from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

import pandas as pd
import requests

from data_provider.eastmoney_fund_dividends import normalize_eastmoney_fund_dividends
from data_provider.eastmoney_fund_event_transport import ENDPOINT, read_event_page, read_literal


MAX_RESPONSE_BYTES = 1024 * 1024
COLUMNS = ["基金代码", "基金简称", "权益登记日", "除息日期", "分红", "分红发放日", "-"]


def _literal(text: str, name: str) -> Any:
    return read_literal(text, name)


def parse_dividend_page(text: str, requested_page: int) -> tuple[list[int], list[list[str]]]:
    info = _literal(text, "pageinfo")
    rows = _literal(text, "jjfh_data")
    if (not isinstance(info, list) or len(info) != 3
            or any(type(value) is not int for value in info)
            or info[0] < 1 or not 1 <= info[1] <= 1000 or info[2] != requested_page
            or requested_page > info[0]):
        raise ValueError("基金分红分页元数据无效")
    if not isinstance(rows, list) or len(rows) > info[1]:
        raise ValueError("基金分红分页行数无效")
    if requested_page < info[0] and len(rows) != info[1]:
        raise ValueError("基金分红中间页面不完整")
    if any(not isinstance(row, list) or len(row) != len(COLUMNS)
           or any(not isinstance(value, str) for value in row) for row in rows):
        raise ValueError("基金分红行格式无效")
    return info, rows


def _read_page(session: requests.Session, year: int, page: int) -> str:
    return read_event_page(session, year=year, page=page, data_type="8", max_bytes=MAX_RESPONSE_BYTES)


def fetch_fund_dividend_observations(
    symbol: str, *, start: str, end: str, max_pages: int = 10,
) -> dict[str, Any]:
    """跨年总页预算耗尽即拒绝；成功读取也不签发事件覆盖完整证明。"""
    lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    if lower > upper or lower.year < 1999 or upper.year - lower.year > 4:
        raise ValueError("基金分红读取范围无效或超过五年")
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("基金分红总页预算必须为 1 至 100")
    if not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbol):
        raise ValueError("基金代码必须包含明确交易所")
    if lower.isoformat() != start or upper.isoformat() != end:
        raise ValueError("基金分红日期必须使用 YYYY-MM-DD")
    all_rows: list[list[str]] = []
    page_evidence = []
    remaining = max_pages
    with requests.Session() as session:
        for year in range(lower.year, upper.year + 1):
            page, expected, seen = 1, None, set()
            while True:
                if remaining == 0:
                    raise ValueError("基金分红总页预算不足")
                text = _read_page(session, year, page)
                remaining -= 1
                info, rows = parse_dividend_page(text, page)
                if expected is None:
                    expected = info[:2]
                    if info[0] - 1 > remaining:
                        raise ValueError("基金分红总页预算不足")
                elif info[:2] != expected:
                    raise ValueError("基金分红读取期间分页元数据变化")
                fingerprint = sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()
                if fingerprint in seen:
                    raise ValueError("基金分红分页重复")
                seen.add(fingerprint)
                page_evidence.append({"year": year, "page": page, "rows": len(rows),
                                      "contentHash": fingerprint})
                all_rows.extend(rows)
                if page == info[0]:
                    break
                page += 1
    revision = sha256(json.dumps(page_evidence, sort_keys=True).encode()).hexdigest()
    result = normalize_eastmoney_fund_dividends(
        pd.DataFrame(all_rows, columns=COLUMNS), symbol, start=start, end=end,
        observed_at=datetime.now(timezone.utc), provider_revision=f"eastmoney-fund-fh:{revision}",
    )
    result["retrieval"] = {"endpoint": ENDPOINT, "pages": page_evidence,
                           "paginationComplete": True, "historicalRevisionsVerified": False}
    result["providerRevision"] = f"eastmoney-fund-fh:{revision}"
    return result
