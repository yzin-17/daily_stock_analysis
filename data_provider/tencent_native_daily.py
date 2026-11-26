"""腾讯 newfqkline 精确日线读取；仅投影响应原生量额。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math
import re
import time
from typing import Any

import pandas as pd
import requests

from .base import DataFetchError, ProviderHTTPError


TENCENT_NATIVE_DAILY_URL = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
)
TENCENT_NATIVE_DAILY_PROTOCOL = "tencent-newfqkline-year-partitions-v3"
MAX_RESPONSE_BYTES = 1_048_576
MAX_PARTITIONS = 16
MAX_ROWS = 3650
REQUEST_ROW_LIMIT = 640
# 2026-11-11的真实响应在640条请求下包含641行；查询数量不是响应资源边界。
MAX_ROWS_PER_RESPONSE = REQUEST_ROW_LIMIT + 1


class TencentDailyResponseError(DataFetchError):
    """腾讯原生日线响应未满足精确数据契约。"""

    code = "invalid_response"


def is_star_market_daily_symbol(symbol: str) -> bool:
    """Return whether Tencent's canonical daily symbol reports volume in shares."""
    return symbol.startswith(("sh688", "sh689"))


def _number(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise TencentDailyResponseError(f"腾讯原生日线 {field} 非法")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise TencentDailyResponseError(f"腾讯原生日线 {field} 非法") from exc
    if not number.is_finite() or (positive and number <= 0) or number < 0:
        raise TencentDailyResponseError(f"腾讯原生日线 {field} 非法")
    return number


def _year_partitions(start: date, end: date) -> list[tuple[date, date]]:
    partitions = []
    current = start
    while current <= end:
        stop = min(end, date(current.year, 12, 31))
        partitions.append((current, stop))
        if len(partitions) > MAX_PARTITIONS:
            raise DataFetchError("腾讯原生日线请求跨越过多年段")
        if stop == end:
            break
        current = date(stop.year + 1, 1, 1)
    return partitions


def _parse_response(
    text: str, *, variable: str, symbol: str, adjustment: str,
    start: date, end: date,
) -> list[dict[str, str]]:
    if len(text.encode("utf-8")) > MAX_RESPONSE_BYTES or not text.startswith(f"{variable}="):
        raise TencentDailyResponseError("腾讯原生日线响应包装或大小非法")
    try:
        payload = json.loads(text[len(variable) + 1:])
    except (TypeError, ValueError) as exc:
        raise TencentDailyResponseError("腾讯原生日线响应 JSON 非法") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("code")) is not int or payload["code"] != 0
        or not isinstance(payload.get("data"), dict)
        or set(payload["data"]) != {symbol}
    ):
        raise TencentDailyResponseError("腾讯原生日线响应身份或状态非法")
    body = payload["data"][symbol]
    key = {"none": "day", "qfq": "qfqday", "hfq": "hfqday"}.get(adjustment)
    if key is None:
        raise DataFetchError("腾讯原生日线复权口径非法")
    if isinstance(body, dict) and key not in body and body.get("day") == [] \
            and not any(basis in body for basis in ("qfqday", "hfqday")):
        # 上游对尚无历史的年份仅返回空原生数组；保留零事实，不转换另一口径。
        return []
    if not isinstance(body, dict) or not isinstance(body.get(key), list):
        raise TencentDailyResponseError("腾讯原生日线响应口径或行结构非法")
    raw_rows = body[key]
    if len(raw_rows) > MAX_ROWS_PER_RESPONSE:
        raise TencentDailyResponseError("腾讯原生日线单段行数超限")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) < 9 or not isinstance(raw[0], str):
            raise TencentDailyResponseError("腾讯原生日线缺少原生成交额或行结构非法")
        try:
            trading_date = date.fromisoformat(raw[0])
        except ValueError as exc:
            raise TencentDailyResponseError("腾讯原生日线日期非法") from exc
        if trading_date.isoformat() != raw[0]:
            raise TencentDailyResponseError("腾讯原生日线日期格式非法")
        if raw[0] in seen:
            raise TencentDailyResponseError("腾讯原生日线日期重复")
        seen.add(raw[0])
        # 上游可能返回范围外历史；校验日期身份后，仅投影请求闭区间。
        if trading_date < start or trading_date > end:
            continue
        open_price = _number(raw[1], "开盘价", positive=True)
        close = _number(raw[2], "收盘价", positive=True)
        high = _number(raw[3], "最高价", positive=True)
        low = _number(raw[4], "最低价", positive=True)
        volume = _number(raw[5], "成交量")
        native_amount = _number(raw[8], "原生成交额")
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            raise TencentDailyResponseError("腾讯原生日线 OHLC 非法")
        # 锁定版 AKShare 对该接口的字段约定：常规股票/ETF 为手，科创板为股；
        # 金额原文字段为万元。真实 ETF 单位准入仍由 G0-M 单独完成。
        shares = volume if is_star_market_daily_symbol(symbol) else volume * 100
        rows.append({
            "date": raw[0],
            "open": str(open_price),
            "close": str(close),
            "high": str(high),
            "low": str(low),
            "volume": str(shares),
            "amount": str(native_amount * 10000),
        })
    return rows


def fetch_tencent_native_daily(
    symbol: str, start_date: str, end_date: str,
    adjustment: str, timeout_seconds: float,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """按日历年分段，整窗校验后返回金额和传输证据。"""
    if adjustment not in {"none", "qfq", "hfq"} or not isinstance(symbol, str) \
            or re.fullmatch(r"(?:sh|sz)\d{6}", symbol) is None:
        raise DataFetchError("腾讯原生日线资产或复权口径不支持")
    try:
        start, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except (TypeError, ValueError) as exc:
        raise DataFetchError("腾讯原生日线请求日期非法") from exc
    if start.isoformat() != start_date or end.isoformat() != end_date or start > end:
        raise DataFetchError("腾讯原生日线请求窗口非法")
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) \
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise DataFetchError("腾讯原生日线总期限非法")
    partitions = _year_partitions(start, end)
    deadline = time.monotonic() + timeout_seconds
    all_rows: list[dict[str, str]] = []
    retrieval_parts: list[dict[str, Any]] = []
    for part_start, part_end in partitions:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DataFetchError("腾讯原生日线总期限已耗尽")
        source_adjustment = "" if adjustment == "none" else adjustment
        variable = f"kline_day{source_adjustment}{part_start.year}"
        param = (
            f"{symbol},day,{part_start.isoformat()},{part_end.isoformat()},"
            f"{REQUEST_ROW_LIMIT},{source_adjustment}"
        )
        try:
            response = requests.get(
                TENCENT_NATIVE_DAILY_URL,
                params={"_var": variable, "param": param, "r": "0.8205512681390605"},
                timeout=remaining,
            )
            response.raise_for_status()
            source_text = response.text
        except requests.Timeout as exc:
            raise TimeoutError("腾讯原生日线请求超时") from exc
        except requests.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            raise ProviderHTTPError("tencent", status) from exc
        except requests.RequestException as exc:
            raise ConnectionError("腾讯原生日线网络请求失败") from exc
        if time.monotonic() > deadline:
            raise DataFetchError("腾讯原生日线总期限已耗尽")
        rows = _parse_response(
            source_text, variable=variable, symbol=symbol,
            adjustment=adjustment, start=part_start, end=part_end,
        )
        all_rows.extend(rows)
        if len(all_rows) > MAX_ROWS:
            raise TencentDailyResponseError("腾讯原生日线整窗行数超限")
        retrieval_parts.append({
            "start": part_start.isoformat(),
            "end": part_end.isoformat(),
            "rows": len(rows),
            "sha256": sha256(source_text.encode("utf-8")).hexdigest(),
        })
        if time.monotonic() > deadline:
            raise DataFetchError("腾讯原生日线总期限已耗尽")
    all_rows.sort(key=lambda row: row["date"])
    if len({row["date"] for row in all_rows}) != len(all_rows):
        raise TencentDailyResponseError("腾讯原生日线跨段日期重复")
    revision = sha256(json.dumps(
        retrieval_parts, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return pd.DataFrame(all_rows, columns=[
        "date", "open", "close", "high", "low", "volume", "amount",
    ]), {
        "endpoint": "newfqkline/get",
        "adjustment": adjustment,
        "partitionComplete": True,
        "partitions": retrieval_parts,
        "revision": revision,
    }
