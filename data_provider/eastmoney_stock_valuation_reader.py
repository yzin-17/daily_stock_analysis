"""有界读取东财股票估值原响应；仅输出当前研究观察，不授予历史可见性。"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import re
import time

import requests


ENDPOINT = "https://datacenter-web.eastmoney.com/api/data/v1/get"
REPORT_NAME = "RPT_VALUEANALYSIS_DET"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_SYMBOL = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)\Z")
_REQUEST = re.compile(r"([0-9]{6})(?:\.(SH|SZ|BJ))?\Z")
_TRADE_DATE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}) 00:00:00\Z")


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("valuation_duplicate_json_key")
        result[key] = value
    return result


def _number(value, *, nonnegative=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("valuation_invalid_number")
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("valuation_invalid_number") from exc
    if not parsed.is_finite() or (nonnegative and parsed < 0):
        raise ValueError("valuation_invalid_number")
    result = float(parsed)
    if not math.isfinite(result):
        raise ValueError("valuation_invalid_number")
    return result


def parse_stock_valuation_page(raw, *, code, expected_symbol=None, page, page_size):
    """以原响应而非 SDK 转换表核对身份、日期、分页和最新行数值。"""
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("valuation_invalid_response_bytes")
    try:
        payload = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("valuation_invalid_json") from exc
    if (not isinstance(payload, dict) or payload.get("success") is not True
            or type(payload.get("code")) is not int or payload["code"] != 0):
        raise ValueError("valuation_source_failure")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise ValueError("valuation_missing_result")
    count, pages, rows = result.get("count"), result.get("pages"), result.get("data")
    if (type(count) is not int or type(pages) is not int or count < 1
            or pages != math.ceil(count / page_size) or not isinstance(rows, list)
            or len(rows) != min(page_size, count - (page - 1) * page_size)):
        raise ValueError("valuation_invalid_pagination")
    parsed_rows = []
    market = None
    for row in rows:
        if not isinstance(row, dict) or row.get("SECURITY_CODE") != code:
            raise ValueError("valuation_identity_mismatch")
        symbol = row.get("SECUCODE")
        if (not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)
                or symbol[:6] != code or (expected_symbol and symbol != expected_symbol)):
            raise ValueError("valuation_identity_mismatch")
        if expected_symbol is None:
            expected_symbol = symbol
        source_market = row.get("TRADE_MARKET")
        if not isinstance(source_market, str) or not source_market:
            raise ValueError("valuation_market_missing")
        if market is None:
            market = source_market
        elif source_market != market:
            raise ValueError("valuation_market_changed")
        raw_date = row.get("TRADE_DATE")
        match = _TRADE_DATE.fullmatch(raw_date) if isinstance(raw_date, str) else None
        if match is None:
            raise ValueError("valuation_invalid_trade_date")
        try:
            trade_date = date.fromisoformat(match.group(1)).isoformat()
        except ValueError as exc:
            raise ValueError("valuation_invalid_trade_date") from exc
        values = {
            "pe_ratio": _number(row.get("PE_TTM")),
            "pb_ratio": _number(row.get("PB_MRQ")),
            "total_mv": _number(row.get("TOTAL_MARKET_CAP"), nonnegative=True),
            "circ_mv": _number(row.get("NOTLIMITED_MARKETCAP_A"), nonnegative=True),
        }
        parsed_rows.append((trade_date, values))
    return count, pages, expected_symbol, market, parsed_rows


def _fetch_page(code, page, page_size, remaining):
    params = {
        "sortColumns": "TRADE_DATE", "sortTypes": "-1", "pageSize": str(page_size),
        "pageNumber": str(page), "reportName": REPORT_NAME, "columns": "ALL",
        "quoteColumns": "", "source": "WEB", "client": "WEB",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    deadline = time.monotonic() + remaining
    with requests.get(ENDPOINT, params=params, timeout=(min(5, remaining), remaining),
                      allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise ValueError("valuation_http_failure")
        chunks, size = [], 0
        for chunk in response.iter_content(16384):
            if time.monotonic() >= deadline:
                raise TimeoutError("valuation_deadline")
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValueError("valuation_response_too_large")
            chunks.append(chunk)
        return b"".join(chunks)


def read_current_stock_valuation(symbol, *, page_size=5000, max_pages=5,
                                 timeout_seconds=20, fetch_page=_fetch_page,
                                 monotonic=time.monotonic):
    """全页核验后只返回最新交易日；交易日和抓取时刻均非历史披露时刻。"""
    match = _REQUEST.fullmatch(symbol) if isinstance(symbol, str) else None
    if match is None:
        raise ValueError("valuation_invalid_requested_symbol")
    if (type(page_size) is not int or not 1 <= page_size <= 5000
            or type(max_pages) is not int or not 1 <= max_pages <= 10
            or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60 or not callable(fetch_page)):
        raise ValueError("valuation_invalid_budget")
    code, suffix = match.groups()
    expected_symbol = f"{code}.{suffix}" if suffix else None
    deadline = monotonic() + timeout_seconds
    count = pages = source_market = latest = previous_date = None
    seen_dates, hashes = set(), []
    total_rows = 0
    page = 1
    while pages is None or page <= pages:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("valuation_deadline")
        raw = fetch_page(code, page, page_size, remaining)
        if monotonic() >= deadline:
            raise TimeoutError("valuation_deadline")
        current_count, current_pages, current_symbol, current_market, rows = (
            parse_stock_valuation_page(raw, code=code, expected_symbol=expected_symbol,
                                       page=page, page_size=page_size))
        if pages is None:
            count, pages = current_count, current_pages
            if pages > max_pages:
                raise ValueError("valuation_page_budget_exceeded")
        elif (current_count, current_pages) != (count, pages):
            raise ValueError("valuation_pagination_changed")
        if source_market is None:
            source_market = current_market
        elif current_market != source_market:
            raise ValueError("valuation_market_changed")
        expected_symbol = current_symbol
        for trade_date, values in rows:
            if trade_date in seen_dates or (previous_date and trade_date >= previous_date):
                raise ValueError("valuation_duplicate_or_unsorted_date")
            seen_dates.add(trade_date)
            if latest is None:
                latest = (trade_date, values)
            previous_date = trade_date
        total_rows += len(rows)
        hashes.append(sha256(raw).hexdigest())
        page += 1
    if total_rows != count or monotonic() >= deadline:
        raise ValueError("valuation_incomplete_read")
    trade_date, values = latest
    if not any(value is not None for value in values.values()):
        raise ValueError("valuation_empty_latest_values")
    retrieval = {"endpoint": ENDPOINT, "reportName": REPORT_NAME, "pageSize": page_size,
                 "count": count, "pages": pages, "pageHashes": hashes, "paginationComplete": True}
    fingerprint = sha256(json.dumps(retrieval, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"symbol": expected_symbol, "sourceMarket": source_market,
            "tradeDate": trade_date, "observedAt": datetime.now(timezone.utc).isoformat(),
            "sourceAvailableAt": None, "historicalVisibilityVerified": False,
            "data": values, "marketCapUnit": "yuan", "currency": "CNY",
            "retrieval": retrieval,
            "contentFingerprint": fingerprint}
