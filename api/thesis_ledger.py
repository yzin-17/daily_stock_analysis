# -*- coding: utf-8 -*-
"""ThesisLedger Contract V1 兼容 API。

该模块只负责把 DSA 的原生数据能力映射成 ThesisLedger 的稳定 HTTP 契约，
不改变 DSA 现有原生路由。契约使用独立 Bearer Token，避免复用管理员会话。
"""

from __future__ import annotations

import math
import os
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request

from src.services.thesis_ledger_control import (
    CONSUMER_NAMESPACE,
    CONTROL_CONTRACT_VERSION,
    ControlContractError,
    ThesisLedgerControlStore,
)

CONTRACT_VERSION = 1
PROVIDER_ID = "akshare"
ENGINE_VERSION = "dsa-thesis-ledger-v1"
LOCAL_FIXTURE_VERSION = "dsa-thesis-ledger-fixture-v1"
_request_id_context: ContextVar[str | None] = ContextVar("thesis_ledger_request_id", default=None)
logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/thesis-ledger",
    tags=["ThesisLedger Contract"],
)


def _error(code: str, message: str, status_code: int, request_id: str | None = None) -> None:
    stable_request_id = request_id or _request_id_context.get() or str(uuid.uuid4())
    raise HTTPException(
        status_code=status_code,
        detail={
            "contractVersion": CONTRACT_VERSION,
            "code": code,
            "message": message,
            "requestId": stable_request_id,
            "diagnosticId": stable_request_id,
        },
    )


def require_contract_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> None:
    """校验 ThesisLedger 专用 token，不依赖 DSA 管理员 session。"""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    _request_id_context.set(request_id)
    expected = os.getenv("THESIS_LEDGER_DSA_TOKEN", "").strip()
    if not expected:
        _error("service_misconfigured", "THESIS_LEDGER_DSA_TOKEN 未配置", 503, request_id)
    if authorization != f"Bearer {expected}":
        _error("unauthorized", "需要有效的 ThesisLedger DSA Bearer Token", 401, request_id)


def require_control_token(
    request: Request,
    authorization: Optional[str] = Header(default=None),
) -> None:
    """校验独立 Control Token；客户端永远不应获得该 token。"""
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    _request_id_context.set(request_id)
    expected = os.getenv("THESIS_LEDGER_CONTROL_TOKEN", "").strip()
    if not expected:
        _error("service_misconfigured", "THESIS_LEDGER_CONTROL_TOKEN 未配置", 503, request_id)
    if authorization != f"Bearer {expected}":
        _error("unauthorized", "需要有效的 ThesisLedger Control Token", 401, request_id)


def _control_store() -> ThesisLedgerControlStore:
    return ThesisLedgerControlStore()


def _control_http_error(error: ControlContractError) -> None:
    raise HTTPException(status_code=error.status_code, detail=error.detail()) from error


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



def _canonical_fund_symbol(symbol: str) -> str:
    value = symbol.strip().upper()
    if value.endswith(".OF"):
        value = value[:-3]
    if not value.isdigit() or len(value) != 6:
        _error("invalid_symbol", f"非法场外基金代码: {symbol}", 422)
    return f"{value}.OF"


def _fixture_fund_nav(symbol: str) -> dict[str, Any]:
    canonical = _canonical_fund_symbol(symbol)
    navs = {
        "000001.OF": 1.2345,
        "110022.OF": 3.4567,
    }
    try:
        unit_nav = navs[canonical]
    except KeyError:
        _error("fixture_not_found", f"没有 {symbol} 的确定性基金净值 fixture", 404)
    nav_date = datetime(2025, 1, 9, 7, 0, tzinfo=timezone.utc).isoformat()
    return {
        "version": 1,
        "symbol": canonical,
        "unitNav": unit_nav,
        "navDate": nav_date,
        "provider": PROVIDER_ID,
        "fetchedAt": _fixture_timestamp(),
        "freshness": "delayed",
    }


def _fixture_fund_nav_history(symbol: str, limit: int = 90) -> list[dict[str, Any]]:
    latest = _fixture_fund_nav(symbol)
    latest_date = datetime.fromisoformat(str(latest["navDate"]).replace("Z", "+00:00"))
    count = min(limit, 3650)
    return [
        {
            **latest,
            "unitNav": round(float(latest["unitNav"]) * (0.98 + index * 0.00025), 4),
            "navDate": (latest_date - timedelta(days=count - index - 1)).isoformat(),
        }
        for index in range(count)
    ]


def _real_fund_nav(symbol: str) -> dict[str, Any]:
    canonical = _canonical_fund_symbol(symbol)
    code = canonical[:-3]
    frame = None
    provider = "akshare"
    fallback_used = False
    if _control_store().effective_policy() is not None:
        try:
            from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

            frame, provider, fallback_used = get_thesis_ledger_runtime().fund_nav(canonical)
        except Exception as exc:  # noqa: BLE001 - runtime maps raw failures to diagnostics.
            code_value = getattr(exc, "code", "upstream_unavailable")
            if code_value == "NO_ELIGIBLE_PROVIDER":
                _error("no_eligible_provider", "当前策略没有可用的基金净值 Provider", 503)
            logger.warning("ThesisLedger fund NAV failed: %s", exc)
            _error("upstream_unavailable", "基金单位净值暂时不可用", 503)
    else:
        try:
            import akshare as ak

            frame = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        except Exception as exc:  # noqa: BLE001 - optional provider boundary.
            logger.warning("Legacy fund NAV failed: %s", exc)
            _error("upstream_unavailable", "基金单位净值暂时不可用", 503)
    if frame is None or getattr(frame, "empty", True):
        _error("upstream_unavailable", f"没有 {canonical} 的单位净值数据", 503)

    date_columns = ("净值日期", "日期", "date", "nav_date")
    nav_columns = ("单位净值", "单位净值(元)", "unit_nav", "nav")
    date_column = next((column for column in date_columns if column in frame.columns), None)
    nav_column = next((column for column in nav_columns if column in frame.columns), None)
    if date_column is None or nav_column is None:
        _error("upstream_invalid_response", "基金净值响应缺少日期或单位净值字段", 502)
    latest = frame.sort_values(date_column).iloc[-1]
    nav_date = _iso_timestamp(latest[date_column])
    unit_nav = _number(latest[nav_column], "unitNav", allow_zero=False)
    parsed_date = datetime.fromisoformat(nav_date.replace("Z", "+00:00"))
    age_days = max(0, (datetime.now(timezone.utc).date() - parsed_date.date()).days)
    freshness = "stale" if age_days > 7 else "delayed"
    return {
        "version": 1,
        "symbol": canonical,
        "unitNav": unit_nav,
        "navDate": nav_date,
        "provider": provider,
        "fetchedAt": _now_iso(),
        "freshness": freshness,
        "fallbackUsed": fallback_used,
    }


def _real_fund_nav_history(
    symbol: str,
    start: Optional[str],
    end: Optional[str],
    limit: int,
) -> list[dict[str, Any]]:
    canonical = _canonical_fund_symbol(symbol)
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

        frame, provider, fallback_used = get_thesis_ledger_runtime().fund_nav_history(canonical)
    except Exception as exc:  # noqa: BLE001 - runtime exposes stable error codes.
        code_value = getattr(exc, "code", "upstream_unavailable")
        if code_value == "NO_ELIGIBLE_PROVIDER":
            _error("no_eligible_provider", "当前策略没有可用的基金净值历史 Provider", 503)
        logger.warning("ThesisLedger fund NAV history failed: %s", exc)
        _error("upstream_unavailable", "基金净值历史暂时不可用", 503)

    date_columns = ("净值日期", "日期", "date", "nav_date")
    nav_columns = ("单位净值", "单位净值(元)", "unit_nav", "nav")
    date_column = next((column for column in date_columns if column in frame.columns), None)
    nav_column = next((column for column in nav_columns if column in frame.columns), None)
    if date_column is None or nav_column is None:
        _error("upstream_invalid_response", "基金净值历史缺少日期或单位净值字段", 502)
    start_at = (
        datetime.fromisoformat(_iso_timestamp(start).replace("Z", "+00:00"))
        if start
        else None
    )
    end_at = (
        datetime.fromisoformat(_iso_timestamp(end).replace("Z", "+00:00"))
        if end
        else None
    )
    fetched_at = _now_iso()
    result: list[dict[str, Any]] = []
    for _, row in frame.sort_values(date_column).iterrows():
        nav_date = _iso_timestamp(row[date_column])
        parsed = datetime.fromisoformat(nav_date.replace("Z", "+00:00"))
        if start_at and parsed < start_at:
            continue
        if end_at and parsed > end_at:
            continue
        age_days = max(0, (datetime.now(timezone.utc).date() - parsed.date()).days)
        result.append(
            {
                "version": 1,
                "symbol": canonical,
                "unitNav": _number(row[nav_column], "unitNav", allow_zero=False),
                "navDate": nav_date,
                "provider": provider,
                "fetchedAt": fetched_at,
                "freshness": "stale" if age_days > 7 else "delayed",
                "fallbackUsed": fallback_used,
            }
        )
    return result[-limit:]

def _daily_data(symbol: str, days: int = 90) -> tuple[Any, str]:
    try:
        return _manager().get_daily_data(symbol, days=days)
    except Exception as exc:  # noqa: BLE001 - adapter maps provider failures to contract errors.
        _error("upstream_unavailable", f"日线数据获取失败: {exc}", 503)


def _real_quote(symbol: str) -> dict[str, Any]:
    if _control_store().effective_policy() is not None:
        try:
            from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

            quote, provider, fallback_used = get_thesis_ledger_runtime().quote(symbol)
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
                "provider": provider,
                "marketTime": market_time,
                "fetchedAt": fetched_at,
                "freshness": _freshness(stale, provider_timestamp),
                "fallbackUsed": fallback_used,
            }
        except Exception as exc:  # noqa: BLE001 - runtime maps raw failures to diagnostics.
            code_value = getattr(exc, "code", "upstream_unavailable")
            if code_value == "NO_ELIGIBLE_PROVIDER":
                _error("no_eligible_provider", "当前策略没有可用的实时行情 Provider", 503)
            logger.warning("ThesisLedger quote failed: %s", exc)
            _error("upstream_unavailable", "实时行情暂时不可用", 503)
    try:
        quote = _manager().get_realtime_quote(symbol)
    except Exception as exc:  # noqa: BLE001 - adapter boundary.
        logger.warning("Legacy quote failed: %s", exc)
        _error("upstream_unavailable", "实时行情暂时不可用", 503)
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
        "fallbackUsed": False,
    }


def _real_bars(symbol: str, start: Optional[str], end: Optional[str], limit: int) -> list[dict[str, Any]]:
    fallback_used = False
    if _control_store().effective_policy() is not None:
        try:
            from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

            result, source, fallback_used = get_thesis_ledger_runtime().bars(symbol, days=limit)
            frame = result
        except Exception as exc:  # noqa: BLE001 - runtime maps raw failures to diagnostics.
            code_value = getattr(exc, "code", "upstream_unavailable")
            if code_value == "NO_ELIGIBLE_PROVIDER":
                _error("no_eligible_provider", "当前策略没有可用的日线 Provider", 503)
            logger.warning("ThesisLedger bars failed: %s", exc)
            _error("upstream_unavailable", "日线数据暂时不可用", 503)
    else:
        frame, source = _daily_data(symbol, days=limit)
    if frame is None or frame.empty:
        _error("upstream_unavailable", f"没有 {symbol} 的日线数据", 503)
    provider = source if isinstance(source, str) else _provider_name(source)
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
                "fallbackUsed": fallback_used,
            }
        )
    result.sort(key=lambda item: item["timestamp"])
    return result[-limit:]


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
        "dataContractVersion": CONTRACT_VERSION,
        "provider": PROVIDER_ID,
        "fixtureMode": _fixture_mode(),
        "controlContract": {
            "version": CONTROL_CONTRACT_VERSION,
            "consumerNamespaces": [CONSUMER_NAMESPACE],
            "requiresIndependentToken": True,
            "routes": [
                "handshake",
                "provider-registry",
                "provider-config",
                "policy-apply",
                "effective-policy",
                "catalog-job",
            ],
        },
        "capabilities": {
            "quote": True,
            "fund-nav": {"assetSuffix": ".OF", "freshness": ["delayed", "stale", "unavailable"]},
            "fund-nav-history": {"assetSuffix": ".OF", "maxLimit": 3650},
            "bars": {"timeframes": ["1d"]},
            "indicators": {"names": ["MA", "MACD", "RSI"], "timeframes": ["1d"]},
            "chip": {"summary": True, "distribution": False},
            "catalog": {"snapshot": True, "delta": True},
        },
        "unsupported": ["bars:1m", "indicator:ATR", "chip:distribution"],
    }



@router.get("/market/fund-nav", dependencies=[Depends(require_contract_token)])
def fund_nav(symbol: str = Query(..., min_length=1)) -> dict[str, Any]:
    return _fixture_fund_nav(symbol) if _fixture_mode() else _real_fund_nav(symbol)


@router.get("/market/fund-nav/history", dependencies=[Depends(require_contract_token)])
def fund_nav_history(
    symbol: str = Query(..., min_length=1),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    limit: int = Query(365, ge=1, le=3650),
) -> list[dict[str, Any]]:
    if _fixture_mode():
        rows = _fixture_fund_nav_history(symbol, limit)
        if start:
            rows = [row for row in rows if str(row["navDate"]) >= start]
        if end:
            rows = [row for row in rows if str(row["navDate"]) <= end]
        return rows
    return _real_fund_nav_history(symbol, start, end, limit)


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


def _require_control_envelope(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        error = ControlContractError("INVALID_ENVELOPE", "Control 请求必须是 JSON 对象")
        _control_http_error(error)
    return payload


def _control_envelope(payload: Any) -> dict[str, Any]:
    value = _require_control_envelope(payload)
    if value.get("contractVersion") != CONTROL_CONTRACT_VERSION:
        error = ControlContractError(
            "CONTROL_CONTRACT_UNSUPPORTED",
            "Control Contract 版本不兼容",
            request_id=str(value.get("requestId") or ""),
        )
        _control_http_error(error)
    if value.get("consumer") != CONSUMER_NAMESPACE:
        error = ControlContractError(
            "INVALID_CONSUMER",
            "Control Contract consumer namespace 不正确",
            request_id=str(value.get("requestId") or ""),
        )
        _control_http_error(error)
    return value


@router.post("/control/handshake", dependencies=[Depends(require_control_token)])
def control_handshake(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """协商独立 Control Contract，不返回任何凭证内容。"""
    try:
        value = _control_envelope(payload)
    except HTTPException:
        raise
    requested = value.get("supportedVersions", [CONTROL_CONTRACT_VERSION])
    supported = CONTROL_CONTRACT_VERSION in requested if isinstance(requested, list) else False
    if not supported:
        error = ControlContractError(
            "CONTROL_CONTRACT_UNSUPPORTED",
            "没有共同的 Control Contract 版本",
            request_id=str(value.get("requestId") or ""),
            details={"supportedVersions": [CONTROL_CONTRACT_VERSION]},
        )
        _control_http_error(error)
    return {
        "contractVersion": CONTROL_CONTRACT_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "accepted": True,
        "providerRegistry": True,
        "policyApply": True,
        "catalogSync": True,
        "requestId": str(value.get("requestId") or _request_id_context.get() or uuid.uuid4()),
    }


@router.get("/control/providers", dependencies=[Depends(require_control_token)])
def control_providers() -> dict[str, Any]:
    return {
        "contractVersion": CONTROL_CONTRACT_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "providers": _control_store().provider_registry(),
    }


@router.post("/control/providers/{provider_id}/config", dependencies=[Depends(require_control_token)])
def control_provider_config(
    provider_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    try:
        value = _control_envelope(payload)
        return _control_store().save_provider_config(provider_id, value)
    except ControlContractError as error:
        _control_http_error(error)


@router.post("/control/providers/{provider_id}/test", dependencies=[Depends(require_control_token)])
def control_provider_test(
    provider_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """执行只读、有限的逐 Capability smoke，并保证临时凭证不落库。"""
    try:
        value = _control_envelope(payload)
        registry = {
            item["providerId"]: item for item in _control_store().provider_registry()
        }
        provider = registry.get(provider_id.strip().lower())
        if provider is None:
            raise ControlContractError(
                "UNKNOWN_PROVIDER",
                f"Provider {provider_id} 不存在",
                request_id=str(value.get("requestId") or ""),
            )
        ephemeral_configured = bool(str(value.get("credential") or "").strip())
        credential_configured = bool(
            provider["credentialConfigured"] or ephemeral_configured
        )
        configured = bool(provider["configured"] or credential_configured)
        if not configured:
            capability_results = {
                capability: {
                    "status": "unconfigured",
                    "readOnly": True,
                    "attempted": False,
                }
                for capability in provider["capabilities"]
            }
            status = "unconfigured"
        else:
            capability_results: dict[str, dict[str, Any]] = {}
            for capability in provider["capabilities"]:
                try:
                    if _fixture_mode():
                        if capability == "REALTIME_QUOTE":
                            _fixture_quote("600519.SH")
                        elif capability == "DAILY_BAR":
                            _fixture_bars("600519.SH")
                        elif capability == "FUND_NAV":
                            _fixture_fund_nav("000001.OF")
                        elif capability == "FUND_NAV_HISTORY":
                            from src.services.thesis_ledger_provider_runtime import (
                                validate_fund_nav_history_rows,
                            )

                            history = _fixture_fund_nav_history("000001.OF", limit=5)
                            validate_fund_nav_history_rows(
                                [(row["navDate"], row["unitNav"]) for row in history]
                            )
                        else:
                            raise ControlContractError(
                                "UNSUPPORTED_CAPABILITY",
                                f"Capability {capability} 不支持",
                                request_id=str(value.get("requestId") or ""),
                            )
                        capability_results[capability] = {
                            "status": "healthy",
                            "readOnly": True,
                            "attempted": True,
                            "source": "fixture",
                        }
                    else:
                        from src.services.thesis_ledger_provider_runtime import (
                            get_thesis_ledger_runtime,
                        )

                        capability_results[capability] = get_thesis_ledger_runtime().smoke(
                            provider["providerId"], capability
                        )
                except ControlContractError:
                    raise
                except Exception as exc:  # runtime maps raw errors to stable diagnostics.
                    code = getattr(exc, "code", "upstream_failure")
                    capability_results[capability] = {
                        "status": "unavailable",
                        "readOnly": True,
                        "attempted": True,
                        "errorCode": code,
                    }
            status = (
                "healthy"
                if all(item["status"] == "healthy" for item in capability_results.values())
                else "degraded"
            )
        return {
            "contractVersion": CONTROL_CONTRACT_VERSION,
            "consumer": CONSUMER_NAMESPACE,
            "providerId": provider["providerId"],
            "status": status,
            "credentialConfigured": credential_configured,
            "capabilityResults": capability_results,
            "requestId": str(value.get("requestId") or _request_id_context.get() or uuid.uuid4()),
        }
    except ControlContractError as error:
        _control_http_error(error)


@router.post("/control/providers/{provider_id}/remove", dependencies=[Depends(require_control_token)])
def control_provider_remove(
    provider_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    try:
        value = _control_envelope(payload)
        return _control_store().remove_provider(provider_id, value)
    except ControlContractError as error:
        _control_http_error(error)


@router.post("/control/policies/apply", dependencies=[Depends(require_control_token)])
def control_apply_policy(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        value = _control_envelope(payload)
        return _control_store().apply_policy(value)
    except ControlContractError as error:
        _control_http_error(error)


@router.get("/control/policies/effective", dependencies=[Depends(require_control_token)])
def control_effective_policy() -> dict[str, Any]:
    return {
        "contractVersion": CONTROL_CONTRACT_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "projection": _control_store().policy_projection(),
    }


@router.get("/catalog/snapshot", dependencies=[Depends(require_contract_token)])
def catalog_snapshot(cursor: Optional[str] = Query(default=None)) -> dict[str, Any]:
    try:
        return _control_store().catalog_snapshot(cursor)
    except ControlContractError as error:
        _control_http_error(error)


@router.get("/catalog/delta", dependencies=[Depends(require_contract_token)])
def catalog_delta(cursor: str = Query(..., min_length=1)) -> dict[str, Any]:
    try:
        return _control_store().catalog_delta(cursor)
    except ControlContractError as error:
        _control_http_error(error)


@router.post("/control/catalog/jobs", dependencies=[Depends(require_control_token)])
def control_catalog_job(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        _control_envelope(payload)
        return _control_store().trigger_catalog_job()
    except ControlContractError as error:
        _control_http_error(error)


@router.post("/control/catalog/ack", dependencies=[Depends(require_control_token)])
def control_catalog_ack(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        _control_envelope(payload)
        return _control_store().catalog_ack(payload)
    except ControlContractError as error:
        _control_http_error(error)
