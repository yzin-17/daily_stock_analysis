# -*- coding: utf-8 -*-
"""ThesisLedger Contract V1 兼容 API。

该模块只负责把 DSA 的原生数据能力映射成 ThesisLedger 的稳定 HTTP 契约，
不改变 DSA 现有原生路由。契约使用独立 Bearer Token，避免复用管理员会话。
"""

from __future__ import annotations

import math
import os
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query

CONTRACT_VERSION = 1
PROVIDER_ID = "dsa-fork"
ENGINE_VERSION = "dsa-thesis-ledger-v1"
LOCAL_FIXTURE_VERSION = "dsa-thesis-ledger-fixture-v1"

router = APIRouter(
    prefix="/thesis-ledger",
    tags=["ThesisLedger Contract"],
)


def _error(code: str, message: str, status_code: int) -> None:
    raise HTTPException(
        status_code=status_code,
        detail={
            "contractVersion": CONTRACT_VERSION,
            "code": code,
            "message": message,
        },
    )


def require_contract_token(authorization: Optional[str] = Header(default=None)) -> None:
    """校验 ThesisLedger 专用 token，不依赖 DSA 管理员 session。"""
    expected = os.getenv("THESIS_LEDGER_DSA_TOKEN", "").strip()
    if not expected:
        _error("service_misconfigured", "THESIS_LEDGER_DSA_TOKEN 未配置", 503)
    if authorization != f"Bearer {expected}":
        _error("unauthorized", "需要有效的 ThesisLedger DSA Bearer Token", 401)


def _fixture_mode() -> bool:
    return os.getenv("THESIS_LEDGER_FIXTURE_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fixture_timestamp() -> str:
    return datetime(2025, 1, 10, 7, 0, tzinfo=timezone.utc).isoformat()


def _canonical_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if value.isdigit() and len(value) == 6:
        if value.startswith(("00", "30")):
            return f"{value}.SZ"
        if value.startswith(("92", "43", "81", "82", "83", "87", "88")):
            return f"{value}.BJ"
        return f"{value}.SH"
    return value


def _fixture_price(symbol: str) -> float:
    prices = {
        "600519.SH": 1488.0,
        "510300.SH": 4.08,
        "000001.SZ": 11.68,
    }
    try:
        return prices[_canonical_symbol(symbol)]
    except KeyError:
        _error("fixture_not_found", f"没有 {symbol} 的确定性 fixture", 404)


def _number(value: Any, field: str, *, allow_zero: bool = True) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        _error("upstream_invalid_response", f"字段 {field} 不是有效数字", 502)
    if not math.isfinite(result) or (not allow_zero and result <= 0):
        _error("upstream_invalid_response", f"字段 {field} 数值非法", 502)
    return result


def _iso_timestamp(value: Any) -> str:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            _error("upstream_invalid_response", "缺少行情时间", 502)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                parsed = datetime.combine(
                    datetime.strptime(text[:10], "%Y-%m-%d").date(),
                    time.min,
                )
            except ValueError:
                _error("upstream_invalid_response", f"行情时间无法解析: {text}", 502)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _provider_name(value: Any, fallback: str = PROVIDER_ID) -> str:
    raw = getattr(value, "value", value)
    text = str(raw or fallback).strip()
    return text or fallback


def _freshness(is_stale: bool, provider_timestamp: Optional[str]) -> str:
    if is_stale:
        return "stale"
    if provider_timestamp:
        return "live"
    return "unknown"


@lru_cache(maxsize=1)
def _manager() -> Any:
    from data_provider.base import DataFetcherManager

    return DataFetcherManager()


def _fixture_quote(symbol: str) -> dict[str, Any]:
    canonical = _canonical_symbol(symbol)
    price = _fixture_price(canonical)
    now = _fixture_timestamp()
    return {
        "version": 1,
        "symbol": canonical,
        "open": round(price * 0.99, 4),
        "high": round(price * 1.01, 4),
        "low": round(price * 0.98, 4),
        "price": price,
        "previousClose": round(price * 0.995, 4),
        "volume": 100000,
        "amount": round(price * 100000, 4),
        "stale": False,
        "provider": PROVIDER_ID,
        "marketTime": now,
        "fetchedAt": now,
        "freshness": "live",
    }


def _fixture_bars(symbol: str) -> list[dict[str, Any]]:
    canonical = _canonical_symbol(symbol)
    price = _fixture_price(canonical)
    end = datetime(2025, 1, 10, 7, 0, tzinfo=timezone.utc)
    result: list[dict[str, Any]] = []
    for index in range(60):
        close = round(price * (0.88 + index * 0.002), 4)
        result.append(
            {
                "version": 1,
                "symbol": canonical,
                "timeframe": "1d",
                "timestamp": (end - timedelta(days=59 - index)).isoformat(),
                "open": round(close * 0.998, 4),
                "high": round(close * 1.005, 4),
                "low": round(close * 0.995, 4),
                "close": close,
                "volume": 100000 + index * 1000,
                "amount": round(close * (100000 + index * 1000), 4),
                "provider": PROVIDER_ID,
            }
        )
    return result


def _fixture_indicator(symbol: str, name: str) -> dict[str, Any]:
    canonical = _canonical_symbol(symbol)
    price = _fixture_price(canonical)
    normalized = name.upper()
    values = {
        "MA": {"ma5": round(price * 0.99, 4), "ma10": round(price * 0.985, 4)},
        "MACD": {"dif": 1.2, "dea": 0.8, "histogram": 0.8},
        "RSI": {"rsi14": 56.4},
    }
    if normalized not in values:
        _error("unsupported_capability", f"指标 {normalized} 在 Contract V1 不可用", 422)
    calculated_at = _fixture_timestamp()
    return {
        "version": 1,
        "symbol": canonical,
        "name": normalized,
        "parameters": {"period": 14 if normalized == "RSI" else 5},
        "timeframe": "1d",
        "marketTime": calculated_at,
        "calculatedAt": calculated_at,
        "values": values[normalized],
        "provider": PROVIDER_ID,
        "engineVersion": LOCAL_FIXTURE_VERSION,
    }


def _fixture_chip(symbol: str) -> dict[str, Any]:
    canonical = _canonical_symbol(symbol)
    price = _fixture_price(canonical)
    calculated_at = _fixture_timestamp()
    return {
        "version": 1,
        "symbol": canonical,
        "buckets": [
            {"price": round(price * 0.9, 4), "weight": 0.2},
            {"price": round(price, 4), "weight": 0.5},
            {"price": round(price * 1.1, 4), "weight": 0.3},
        ],
        "averageCost": round(price * 0.99, 4),
        "mainPeak": price,
        "profitRatio": 0.58,
        "range70": [round(price * 0.92, 4), round(price * 1.06, 4)],
        "range90": [round(price * 0.88, 4), round(price * 1.12, 4)],
        "concentration": 0.32,
        "provider": PROVIDER_ID,
        "engineVersion": LOCAL_FIXTURE_VERSION,
        "calculatedAt": calculated_at,
    }


def _daily_data(symbol: str, days: int = 90) -> tuple[Any, str]:
    try:
        return _manager().get_daily_data(symbol, days=days)
    except Exception as exc:  # noqa: BLE001 - adapter maps provider failures to contract errors.
        _error("upstream_unavailable", f"日线数据获取失败: {exc}", 503)


def _real_quote(symbol: str) -> dict[str, Any]:
    try:
        quote = _manager().get_realtime_quote(symbol)
    except Exception as exc:  # noqa: BLE001 - adapter boundary.
        _error("upstream_unavailable", f"实时行情获取失败: {exc}", 503)
    if quote is None or getattr(quote, "price", None) is None:
        _error("upstream_unavailable", f"没有 {symbol} 的实时行情", 503)

    fetched_at = _iso_timestamp(getattr(quote, "fetched_at", None) or _now_iso())
    provider_timestamp = getattr(quote, "provider_timestamp", None)
    market_time = _iso_timestamp(provider_timestamp or fetched_at)
    stale = bool(getattr(quote, "is_stale", False))
    fields = {
        "open": getattr(quote, "open_price", None),
        "high": getattr(quote, "high", None),
        "low": getattr(quote, "low", None),
        "price": getattr(quote, "price", None),
        "previousClose": getattr(quote, "pre_close", None),
        "volume": getattr(quote, "volume", None),
        "amount": getattr(quote, "amount", None),
    }
    values = {key: _number(value, key) for key, value in fields.items()}
    return {
        "version": 1,
        "symbol": _canonical_symbol(symbol),
        **values,
        "stale": stale,
        "provider": _provider_name(getattr(quote, "source", None)),
        "marketTime": market_time,
        "fetchedAt": fetched_at,
        "freshness": _freshness(stale, provider_timestamp),
    }


def _real_bars(symbol: str, start: Optional[str], end: Optional[str], limit: int) -> list[dict[str, Any]]:
    frame, source = _daily_data(symbol, days=limit)
    if frame is None or frame.empty:
        _error("upstream_unavailable", f"没有 {symbol} 的日线数据", 503)
    provider = _provider_name(source)
    result: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        date_value = row.get("date")
        timestamp = _iso_timestamp(date_value)
        day = timestamp[:10]
        if start and day < start[:10]:
            continue
        if end and day > end[:10]:
            continue
        close = _number(row.get("close"), "close")
        result.append(
            {
                "version": 1,
                "symbol": _canonical_symbol(symbol),
                "timeframe": "1d",
                "timestamp": timestamp,
                "open": _number(row.get("open"), "open"),
                "high": _number(row.get("high"), "high"),
                "low": _number(row.get("low"), "low"),
                "close": close,
                "volume": _number(row.get("volume"), "volume"),
                "amount": _number(row.get("amount"), "amount"),
                "provider": provider,
            }
        )
    result.sort(key=lambda item: item["timestamp"])
    return result


def _real_indicator(symbol: str, name: str) -> dict[str, Any]:
    normalized = name.upper()
    if normalized not in {"MA", "MACD", "RSI"}:
        _error("unsupported_capability", f"指标 {normalized} 在 Contract V1 不可用", 422)
    frame, source = _daily_data(symbol, days=90)
    if frame is None or frame.empty:
        _error("upstream_unavailable", f"没有 {symbol} 的指标输入数据", 503)
    try:
        from src.stock_analyzer import StockTrendAnalyzer

        analysis = StockTrendAnalyzer().analyze(frame, _canonical_symbol(symbol))
        latest = frame.iloc[-1].get("date")
        values = {
            "MA": {
                "ma5": analysis.ma5,
                "ma10": analysis.ma10,
                "ma20": analysis.ma20,
                "ma60": analysis.ma60,
            },
            "MACD": {
                "dif": analysis.macd_dif,
                "dea": analysis.macd_dea,
                "histogram": analysis.macd_bar,
            },
            "RSI": {
                "rsi6": analysis.rsi_6,
                "rsi12": analysis.rsi_12,
                "rsi24": analysis.rsi_24,
            },
        }[normalized]
    except Exception as exc:  # noqa: BLE001 - adapter boundary.
        _error("upstream_invalid_response", f"指标计算失败: {exc}", 502)
    return {
        "version": 1,
        "symbol": _canonical_symbol(symbol),
        "name": normalized,
        "parameters": {"period": 14 if normalized == "RSI" else 5},
        "timeframe": "1d",
        "marketTime": _iso_timestamp(latest),
        "calculatedAt": _now_iso(),
        "values": {key: _number(value, key) for key, value in values.items()},
        "provider": _provider_name(source),
        "engineVersion": ENGINE_VERSION,
    }


def _ratio(value: Any, field: str) -> float:
    result = _number(value, field)
    if result > 1:
        result /= 100
    if not 0 <= result <= 1:
        _error("upstream_invalid_response", f"字段 {field} 不在 0 到 1 之间", 502)
    return result


def _real_chip(symbol: str) -> dict[str, Any]:
    try:
        chip = _manager().get_chip_distribution(symbol)
    except Exception as exc:  # noqa: BLE001 - adapter boundary.
        _error("upstream_unavailable", f"筹码数据获取失败: {exc}", 503)
    if chip is None:
        _error("upstream_unavailable", f"没有 {symbol} 的筹码摘要", 503)
    average_cost = _number(getattr(chip, "avg_cost", None), "averageCost", allow_zero=False)
    range70 = [
        _number(getattr(chip, "cost_70_low", None), "range70.low", allow_zero=False),
        _number(getattr(chip, "cost_70_high", None), "range70.high", allow_zero=False),
    ]
    range90 = [
        _number(getattr(chip, "cost_90_low", None), "range90.low", allow_zero=False),
        _number(getattr(chip, "cost_90_high", None), "range90.high", allow_zero=False),
    ]
    return {
        "version": 1,
        "symbol": _canonical_symbol(symbol),
        "averageCost": average_cost,
        "profitRatio": _ratio(getattr(chip, "profit_ratio", None), "profitRatio"),
        "range70": range70,
        "range90": range90,
        "concentration": _ratio(
            getattr(chip, "concentration_90", None),
            "concentration",
        ),
        "provider": _provider_name(getattr(chip, "source", None)),
        "engineVersion": ENGINE_VERSION,
        "calculatedAt": _iso_timestamp(getattr(chip, "date", None) or _now_iso()),
    }


@router.get("/capabilities", dependencies=[Depends(require_contract_token)])
def capabilities() -> dict[str, Any]:
    return {
        "contractVersion": CONTRACT_VERSION,
        "provider": PROVIDER_ID,
        "fixtureMode": _fixture_mode(),
        "capabilities": {
            "quote": True,
            "bars": {"timeframes": ["1d"]},
            "indicators": {"names": ["MA", "MACD", "RSI"], "timeframes": ["1d"]},
            "chip": {"summary": True, "distribution": False},
        },
        "unsupported": ["bars:1m", "indicator:ATR", "chip:distribution"],
    }


@router.get("/market/quote", dependencies=[Depends(require_contract_token)])
def quote(symbol: str = Query(..., min_length=1)) -> dict[str, Any]:
    return _fixture_quote(symbol) if _fixture_mode() else _real_quote(symbol)


@router.get("/market/bars", dependencies=[Depends(require_contract_token)])
def bars(
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("1d"),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    limit: int = Query(90, ge=1, le=365),
) -> list[dict[str, Any]]:
    if timeframe != "1d":
        _error("unsupported_capability", "Contract V1 只支持 1d bars", 422)
    if _fixture_mode():
        result = _fixture_bars(symbol)
        filtered = [
            item
            for item in result
            if (not start or item["timestamp"][:10] >= start[:10])
            and (not end or item["timestamp"][:10] <= end[:10])
        ]
        return filtered[-limit:]
    return _real_bars(symbol, start, end, limit)


@router.get("/market/indicators/{name}", dependencies=[Depends(require_contract_token)])
def indicator(
    name: str,
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("1d"),
) -> dict[str, Any]:
    if timeframe != "1d":
        _error("unsupported_capability", "Contract V1 只支持 1d indicators", 422)
    return _fixture_indicator(symbol, name) if _fixture_mode() else _real_indicator(symbol, name)


@router.get("/market/chip", dependencies=[Depends(require_contract_token)])
def chip(symbol: str = Query(..., min_length=1)) -> dict[str, Any]:
    return _fixture_chip(symbol) if _fixture_mode() else _real_chip(symbol)
