"""东财基金拆分有界读取；传输完整不等于经济事件完整。"""

from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
from typing import Any

import pandas as pd
import requests

from data_provider.eastmoney_fund_event_transport import ENDPOINT, read_event_page, read_literal
from data_provider.eastmoney_fund_splits import normalize_eastmoney_fund_splits

COLUMNS = ["基金代码", "基金简称", "拆分折算日", "拆分类型", "拆分折算", "-"]


def parse_split_page(text: str, page: int) -> tuple[list[int], list[list[Any]]]:
    info, rows = read_literal(text, "pageinfo"), read_literal(text, "jjcf_data")
    if (not isinstance(info, list) or len(info) != 3
            or any(type(value) is not int for value in info)
            or info[0] < 1 or not 1 <= info[1] <= 1000
            or info[2] != page or not 1 <= page <= info[0]):
        raise ValueError("基金拆分分页元数据无效")
    if (not isinstance(rows, list) or len(rows) > info[1]
            or (page < info[0] and len(rows) != info[1])):
        raise ValueError("基金拆分页面行数不完整")
    for row in rows:
        if (not isinstance(row, list) or len(row) != len(COLUMNS)
                or any(not isinstance(row[index], str) for index in (0, 1, 2, 3, 5))
                or isinstance(row[4], bool) or not isinstance(row[4], (str, int, Decimal))):
            raise ValueError("基金拆分行格式无效")
    return info, rows


def fetch_fund_split_observations(symbol: str, *, start: str, end: str,
                                  max_pages: int = 10) -> dict[str, Any]:
    lower, upper = date.fromisoformat(start), date.fromisoformat(end)
    if (lower > upper or lower.year < 2005 or upper.year - lower.year > 4
            or lower.isoformat() != start or upper.isoformat() != end):
        raise ValueError("基金拆分范围无效或超过五年")
    if not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbol):
        raise ValueError("基金拆分代码必须包含明确交易所")
    if type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ValueError("基金拆分总页预算必须为 1 至 100")
    rows, evidence = [], []
    with requests.Session() as session:
        for year in range(lower.year, upper.year + 1):
            page, expected, seen = 1, None, set()
            while True:
                if len(evidence) >= max_pages:
                    raise ValueError("基金拆分总页预算不足")
                text = read_event_page(session, year=year, page=page, data_type="9", max_bytes=1024 * 1024)
                info, current = parse_split_page(text, page)
                if expected is None:
                    expected = info[:2]
                    if len(evidence) + info[0] > max_pages:
                        raise ValueError("基金拆分总页预算不足")
                elif info[:2] != expected:
                    raise ValueError("基金拆分读取期间分页变化")
                digest = sha256(json.dumps(current, ensure_ascii=False, default=str).encode()).hexdigest()
                if digest in seen:
                    raise ValueError("基金拆分页面重复")
                seen.add(digest)
                evidence.append({"year": year, "page": page, "rows": len(current), "contentHash": digest})
                rows.extend(current)
                if page == info[0]:
                    break
                page += 1
    revision = "eastmoney-fund-cf:" + sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
    result = normalize_eastmoney_fund_splits(
        pd.DataFrame(rows, columns=COLUMNS), symbol, start=start, end=end,
        observed_at=datetime.now(timezone.utc), provider_revision=revision,
    )
    result["providerRevision"] = revision
    result["retrieval"] = {"endpoint": ENDPOINT, "pages": evidence,
                           "paginationComplete": True, "historicalRevisionsVerified": False}
    return result
