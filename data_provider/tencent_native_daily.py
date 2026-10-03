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

from .base import DataFetchError


TENCENT_NATIVE_DAILY_URL = (
    "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
)
TENCENT_NATIVE_DAILY_PROTOCOL = "tencent-newfqkline-year-partitions-v2"
MAX_RESPONSE_BYTES = 1_048_576
MAX_PARTITIONS = 8
MAX_ROWS = 800
MAX_ROWS_PER_RESPONSE = 640


def _number(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise DataFetchError(f"腾讯原生日线 {field} 非法")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise DataFetchError(f"腾讯原生日线 {field} 非法") from exc
    if not number.is_finite() or (positive and number <= 0) or number < 0:
        raise DataFetchError(f"腾讯原生日线 {field} 非法")
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
        raise DataFetchError("腾讯原生日线响应包装或大小非法")
    try:
        payload = json.loads(text[len(variable) + 1:])
    except (TypeError, ValueError) as exc:
        raise DataFetchError("腾讯原生日线响应 JSON 非法") from exc
    if (
        not isinstance(payload, dict)
        or type(payload.get("code")) is not int or payload["code"] != 0
        or not isinstance(payload.get("data"), dict)
        or set(payload["data"]) != {symbol}
    ):
        raise DataFetchError("腾讯原生日线响应身份或状态非法")
    body = payload["data"][symbol]
    key = {"none": "day", "qfq": "qfqday", "hfq": "hfqday"}.get(adjustment)
    if key is None:
        raise DataFetchError("腾讯原生日线复权口径非法")
    if not isinstance(body, dict) or not isinstance(body.get(key), list):
        raise DataFetchError("腾讯原生日线响应口径或行结构非法")
    raw_rows = body[key]
    if len(raw_rows) > MAX_ROWS_PER_RESPONSE:
        raise DataFetchError("腾讯原生日线单段行数超限")
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in raw_rows:
        if not isinstance(raw, list) or len(raw) < 9 or not isinstance(raw[0], str):
            raise DataFetchError("腾讯原生日线缺少原生成交额或行结构非法")
        try:
            trading_date = date.fromisoformat(raw[0])
        except ValueError as exc:
            raise DataFetchError("腾讯原生日线日期非法") from exc
        if trading_date.isoformat() != raw[0]:
            raise DataFetchError("腾讯原生日线日期格式非法")
        if trading_date < start or trading_date > end:
            continue
        if raw[0] in seen:
            raise DataFetchError("腾讯原生日线日期重复")
        seen.add(raw[0])
        open_price = _number(raw[1], "开盘价", positive=True)
        close = _number(raw[2], "收盘价", positive=True)
        high = _number(raw[3], "最高价", positive=True)
        low = _number(raw[4], "最低价", positive=True)
        volume = _number(raw[5], "成交量")
        native_amount = _number(raw[8], "原生成交额")
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            raise DataFetchError("腾讯原生日线 OHLC 非法")
        # 锁定版 AKShare 对该接口的字段约定：常规股票/ETF 为手，科创板为股；
        # 金额原文字段为万元。真实 ETF 单位准入仍由 G0-M 单独完成。
        shares = volume if symbol.startswith("sh688") else volume * 100
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
        variable = f"kline_day{adjustment}{part_start.year}"
        param = (
            f"{symbol},day,{part_start.isoformat()},{part_end.isoformat()},"
            f"{MAX_ROWS_PER_RESPONSE},{'' if adjustment == 'none' else adjustment}"
        )
        try:
            response = requests.get(
                TENCENT_NATIVE_DAILY_URL,
                params={"_var": variable, "param": param},
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/plain,*/*"},
                timeout=remaining,
            )
            response.raise_for_status()
            source_text = response.text
        except requests.RequestException as exc:
            raise DataFetchError("腾讯原生日线请求失败") from exc
        if time.monotonic() > deadline:
            raise DataFetchError("腾讯原生日线总期限已耗尽")
        rows = _parse_response(
            source_text, variable=variable, symbol=symbol,
            adjustment=adjustment, start=part_start, end=part_end,
        )
        all_rows.extend(rows)
        if len(all_rows) > MAX_ROWS:
            raise DataFetchError("腾讯原生日线整窗行数超限")
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
        raise DataFetchError("腾讯原生日线跨段日期重复")
    revision = sha256(json.dumps(
        retrieval_parts, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return pd.DataFrame(all_rows), {
        "endpoint": "newfqkline/get",
        "adjustment": adjustment,
        "partitionComplete": True,
        "partitions": retrieval_parts,
        "revision": revision,
    }
