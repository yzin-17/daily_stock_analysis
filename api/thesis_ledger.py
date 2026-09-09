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
import hashlib
import json
import re
from decimal import Decimal
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request

from src.services.thesis_ledger_control import (
    CONSUMER_NAMESPACE,
    CONTROL_CONTRACT_VERSION,
    ControlContractError,
    ThesisLedgerControlStore,
)
from src.services.thesis_ledger_v2_dependencies import (
    V2DependencyError,
    calendar_fact,
    fixture_calendar,
    parse_data_as_of,
    real_corporate_actions,
    response as v2_response,
    static_cn_instrument_fact,
    validate_cn_stock,
    validate_range,
)

CONTRACT_VERSION = 1
PROVIDER_ID = "akshare"
ENGINE_VERSION = "dsa-thesis-ledger-v1"
LOCAL_FIXTURE_VERSION = "dsa-thesis-ledger-fixture-v1"
FX_CURRENCIES = {"CNY", "HKD", "USD"}
FX_MAX_AGE_DAYS = 7
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


def _data_gateway_error(
    exc: Exception,
    message: str,
    *,
    no_eligible_message: str | None = None,
) -> None:
    """Map gateway failures to stable Data Contract errors without raw details."""
    code_value = getattr(exc, "code", "upstream_unavailable")
    request_id = getattr(exc, "request_id", None)
    if code_value == "NO_ELIGIBLE_PROVIDER":
        _error(
            "no_eligible_provider",
            no_eligible_message or "当前策略没有可用的 Provider",
            503,
            request_id,
        )
    if code_value in {"invalid_response", "upstream_invalid_response"}:
        _error("upstream_invalid_response", "Provider 返回了无效数据", 502, request_id)
    if code_value == "unsupported_capability":
        _error("unsupported_capability", message, 422, request_id)
    logger.warning("ThesisLedger data gateway failed: %s", exc)
    _error("upstream_unavailable", message, 503, request_id)


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


def _backtest_market_for_symbol(symbol: str) -> str:
    canonical = symbol.strip().upper()
    if canonical.endswith(".HK"):
        return "HK"
    if canonical.endswith(".US") or re.match(r"^[A-Z]{1,6}$", canonical):
        return "US"
    return "CN"


def _real_daily_bar_provider_route() -> tuple[str, ...]:
    """Return eligible registry providers for the supported CN daily-bar slice."""
    try:
        route = _control_store().route("DAILY_BAR", "STOCK")
    except Exception as exc:  # registry is an availability input, never a reason to claim support
        logger.warning("读取 ThesisLedger DAILY_BAR Provider route 失败: %s", exc)
        return ()
    return tuple(str(provider).strip() for provider in route if str(provider).strip())


def _real_v2_raw_provider_route() -> tuple[str, ...]:
    """Keep only registry providers exposing the explicit V2 raw-bar method."""
    eligible: list[str] = []
    try:
        from src.services.thesis_ledger_provider_runtime import _PROVIDER_ADAPTER_IMPORTS

        for provider_id in _real_daily_bar_provider_route():
            try:
                adapter_import = _PROVIDER_ADAPTER_IMPORTS.get(provider_id)
                if adapter_import is None:
                    continue
                module_name, class_name = adapter_import
                module = __import__(module_name, fromlist=[class_name])
                adapter_class = getattr(module, class_name, None)
                if callable(getattr(adapter_class, "get_daily_data_v2_raw", None)):
                    eligible.append(provider_id)
            except Exception as exc:
                logger.warning("Provider %s 未声明 V2 raw 日线: %s", provider_id, exc)
    except Exception as exc:
        logger.warning("读取 V2 raw 日线 Provider 能力失败: %s", exc)
    return tuple(eligible)


def _backtest_capabilities() -> dict[str, Any]:
    """Return the V2 data contract without claiming provider data is live.

    DSA owns base facts and capability metadata.  Derived intraday windows are
    deliberately marked as Server-owned so they cannot be mistaken for a DSA
    provider timeframe.
    """
    fixture_mode = _fixture_mode()
    generated_at = _fixture_timestamp() if fixture_mode else _now_iso()
    daily_ranges = {"start": "2024-11-12", "end": "2025-01-10"}
    intraday_ranges = {"start": "2025-01-10", "end": "2025-01-10"}
    unavailable_ranges = {"start": None, "end": None}
    calendar_ranges = intraday_ranges if fixture_mode else unavailable_ranges
    provider_revision = (
        "dsa-backtest-v2-fixture-1"
        if fixture_mode
        else "dsa-backtest-v2-contract-unavailable"
    )
    real_v2_raw_route = _real_v2_raw_provider_route() if not fixture_mode else ()
    capabilities: list[dict[str, Any]] = []
    for market, timezone_name in (
        ("CN", "Asia/Shanghai"),
        ("HK", "Asia/Hong_Kong"),
        ("US", "America/New_York"),
    ):
        for instrument_type in ("STOCK", "ETF"):
            for timeframe in ("1m", "1d"):
                base_status = "supported"
                base_reason = None
                if not fixture_mode:
                    base_status = "unavailable"
                    base_reason = "当前 Provider registry/health 未确认该基础周期可用"
                    if (
                        market == "CN"
                        and instrument_type == "STOCK"
                        and timeframe == "1d"
                        and real_v2_raw_route
                    ):
                        base_status = "supported"
                        base_reason = None
                capability_provider = PROVIDER_ID
                capability_revision = provider_revision
                if base_status == "supported" and not fixture_mode:
                    capability_provider = real_v2_raw_route[0]
                    capability_revision = "dsa-backtest-v2-provider-registry-raw-1"
                capabilities.append(
                    {
                        "market": market,
                        "instrumentType": instrument_type,
                        "timeframe": timeframe,
                        "kind": "base",
                        "status": base_status,
                        "provider": capability_provider,
                        "providerRevision": capability_revision,
                        "range": (
                            intraday_ranges if timeframe == "1m" else daily_ranges
                        )
                        if fixture_mode
                        else unavailable_ranges,
                        "freshness": "unknown",
                        "quality": "unknown",
                        "completeness": "unavailable" if base_status == "unavailable" else "partial",
                        "timezone": timezone_name,
                        **({} if base_reason is None else {"reason": base_reason}),
                    }
                )
            for timeframe in ("5m", "15m", "30m", "60m"):
                capabilities.append(
                    {
                        "market": market,
                        "instrumentType": instrument_type,
                        "timeframe": timeframe,
                        "kind": "derived",
                        "status": "unsupported",
                        "provider": "thesis-ledger-server",
                        "providerRevision": "server-aggregation-v2",
                        "range": {"start": None, "end": None},
                        "freshness": "unknown",
                        "quality": "unknown",
                        "completeness": "unavailable",
                        "timezone": timezone_name,
                        "reason": "派生分钟周期由 Server 从冻结 1m 派生",
                    }
                )
        nav_fixture_supported = market == "CN" and fixture_mode
        nav_status = "supported" if nav_fixture_supported else "unsupported"
        nav_reason = None
        if market == "CN" and not fixture_mode:
            nav_status = "unavailable"
            nav_reason = "当前 Provider registry/health 未确认 CN NAV 可用"
        elif market != "CN":
            nav_status = "unsupported"
            nav_reason = "V2 仅支持中国内地 NAV Fund"
        nav_capability = {
            "market": market,
            "instrumentType": "NAV_FUND",
            "timeframe": "1d",
            "kind": "base",
            "status": nav_status,
            "provider": PROVIDER_ID,
            "providerRevision": provider_revision,
            "range": (
                {"start": "2025-01-09", "end": "2025-01-09"}
                if nav_fixture_supported
                else unavailable_ranges
            ),
            "freshness": "delayed" if nav_fixture_supported else "unknown",
            "quality": "complete" if nav_fixture_supported else "unknown",
            "completeness": "complete" if nav_fixture_supported else "unavailable",
            "timezone": timezone_name,
        }
        if nav_reason is not None:
            nav_capability["reason"] = nav_reason
        capabilities.append(nav_capability)

    calendars = [
        {
            "market": "CN",
            "timezone": "Asia/Shanghai",
            "provider": "exchange-calendar",
            "providerRevision": "fixture-calendar-2025-01-10",
            "sessions": [
                {"startMinute": 570, "endMinute": 690},
                {"startMinute": 780, "endMinute": 900},
            ],
            "holidays": [],
            "range": calendar_ranges,
        },
        {
            "market": "HK",
            "timezone": "Asia/Hong_Kong",
            "provider": "exchange-calendar",
            "providerRevision": "fixture-calendar-2025-01-10",
            "sessions": [
                {"startMinute": 570, "endMinute": 720},
                {"startMinute": 780, "endMinute": 960},
            ],
            "holidays": [],
            "range": calendar_ranges,
        },
        {
            "market": "US",
            "timezone": "America/New_York",
            "provider": "exchange-calendar",
            "providerRevision": "fixture-calendar-2025-01-10",
            "sessions": [{"startMinute": 570, "endMinute": 960}],
            "holidays": [],
            "range": calendar_ranges,
        },
    ]
    instrument_facts = [
        {
            "symbol": "600519.SH",
            "market": "CN",
            "instrumentType": "STOCK",
            "currency": "CNY",
            "lotSize": "100",
            "tickSize": "0.01",
            "tradable": True,
            "provider": PROVIDER_ID,
            "providerRevision": "instrument-v2-fixture-1",
            "occurredAt": generated_at,
            "availableAt": generated_at,
        },
        {
            "symbol": "00005.HK",
            "market": "HK",
            "instrumentType": "STOCK",
            "currency": "HKD",
            "lotSize": "500",
            "tickSize": "0.01",
            "tradable": True,
            "provider": PROVIDER_ID,
            "providerRevision": "instrument-v2-fixture-1",
            "occurredAt": generated_at,
            "availableAt": generated_at,
        },
        {
            "symbol": "AAPL.US",
            "market": "US",
            "instrumentType": "STOCK",
            "currency": "USD",
            "lotSize": "1",
            "tickSize": "0.01",
            "tradable": True,
            "provider": PROVIDER_ID,
            "providerRevision": "instrument-v2-fixture-1",
            "occurredAt": generated_at,
            "availableAt": generated_at,
        },
    ]
    fx_facts = [
        {
            "fromCurrency": "HKD",
            "toCurrency": "CNY",
            "rate": "0.92",
            "occurredAt": generated_at,
            "availableAt": generated_at,
            "provider": PROVIDER_ID,
            "providerRevision": "fx-v2-fixture-1",
            "freshness": "delayed",
            "quality": "complete",
        },
        {
            "fromCurrency": "USD",
            "toCurrency": "CNY",
            "rate": "7.2",
            "occurredAt": generated_at,
            "availableAt": generated_at,
            "provider": PROVIDER_ID,
            "providerRevision": "fx-v2-fixture-1",
            "freshness": "delayed",
            "quality": "complete",
        },
    ]
    corporate_actions = [
        {
            "symbol": "600519.SH",
            "type": "CASH_DIVIDEND",
            "cashAmount": "1.0",
            "currency": "CNY",
            "occurredAt": generated_at,
            "availableAt": generated_at,
            "provider": PROVIDER_ID,
            "providerRevision": "corporate-action-v2-fixture-1",
        }
    ]
    nav_facts = [
        {
            "symbol": "000001.OF",
            "market": "CN",
            "instrumentType": "NAV_FUND",
            "nav": "1.2345",
            "valuationDate": "2025-01-09",
            "occurredAt": "2025-01-09T07:00:00+00:00",
            "availableAt": generated_at,
            "provider": PROVIDER_ID,
            "providerRevision": "nav-v2-fixture-1",
            "freshness": "delayed",
            "quality": "complete",
            "status": "supported",
        }
    ]
    if not fixture_mode:
        instrument_facts = []
        calendars = []
        fx_payload = {
            "status": "unavailable",
            "facts": [],
            "reason": "V2 FX facts 尚未由 Provider registry/health 确认",
        }
        corporate_action_payload = {
            "status": "unavailable",
            "facts": [],
            "reason": "V2 公司行动 Provider 尚未接入",
        }
        nav_payload = {
            "status": "unavailable",
            "facts": [],
            "reason": "V2 NAV facts 尚未由 Provider registry/health 确认",
        }
    else:
        fx_payload = {"status": "supported", "facts": fx_facts}
        corporate_action_payload = {"status": "supported", "facts": corporate_actions}
        nav_payload = {"status": "supported", "facts": nav_facts}
    return {
        "version": 2,
        "provider": PROVIDER_ID,
        "generatedAt": generated_at,
        "capabilities": capabilities,
        "calendars": calendars,
        "instrumentFacts": instrument_facts,
        "fx": fx_payload,
        "corporateActions": corporate_action_payload,
        "nav": nav_payload,
    }


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
        "00005.HK": 300.0,
        "AAPL.US": 200.0,
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


def _decimal_string(value: Any, field: str) -> str:
    """Serialize provider numerics without crossing the JSON Number boundary."""
    raw = value.item() if hasattr(value, "item") else value
    try:
        parsed = Decimal(str(raw))
    except (TypeError, ValueError, ArithmeticError):
        _error("upstream_invalid_response", f"字段 {field} 不是有效数字", 502)
    if not parsed.is_finite():
        _error("upstream_invalid_response", f"字段 {field} 数值非法", 502)
    if parsed.is_zero():
        return "0"
    rendered = format(parsed, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


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


def _upstream_source(value: Any) -> Optional[str]:
    """把适配器内部来源归一为稳定、可展示的上游来源 ID。"""
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip().lower()
    aliases = {
        "eastmoney": "eastmoney",
        "akshare_em": "eastmoney",
        "efinance": "eastmoney",
        "sina": "sina",
        "akshare_sina": "sina",
        "akshare_qq": "tencent",
        "tencent": "tencent",
    }
    return aliases.get(normalized)


def _freshness(is_stale: bool, provider_timestamp: Optional[str]) -> str:
    if is_stale:
        return "stale"
    if provider_timestamp:
        return "live"
    return "unknown"


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


def _fixture_minute_bars(symbol: str) -> list[dict[str, Any]]:
    canonical = symbol.strip().upper()
    market = _backtest_market_for_symbol(canonical)
    timezone_name = {
        "CN": "Asia/Shanghai",
        "HK": "Asia/Hong_Kong",
        "US": "America/New_York",
    }[market]
    sessions = {
        "CN": ((9, 30, 11, 30), (13, 0, 15, 0)),
        "HK": ((9, 30, 12, 0), (13, 0, 16, 0)),
        "US": ((9, 30, 16, 0),),
    }[market]
    local_day = date(2025, 1, 10)
    result: list[dict[str, Any]] = []
    index = 0
    for start_hour, start_minute, end_hour, end_minute in sessions:
        cursor = datetime(
            local_day.year,
            local_day.month,
            local_day.day,
            start_hour,
            start_minute,
            tzinfo=ZoneInfo(timezone_name),
        )
        end = datetime(
            local_day.year,
            local_day.month,
            local_day.day,
            end_hour,
            end_minute,
            tzinfo=ZoneInfo(timezone_name),
        )
        while cursor < end and index < 240:
            close = Decimal("100") + Decimal(index) / Decimal("100")
            occurred_at = cursor.astimezone(timezone.utc).isoformat()
            result.append(
                {
                    "version": 2,
                    "symbol": canonical,
                    "market": market,
                    "timeframe": "1m",
                    "occurredAt": occurred_at,
                    "availableAt": occurred_at,
                    "timestamp": occurred_at,
                    "open": format(close - Decimal("0.01"), "f"),
                    "high": format(close + Decimal("0.02"), "f"),
                    "low": format(close - Decimal("0.02"), "f"),
                    "close": format(close, "f"),
                    "volume": str(1000 + index),
                    "amount": format(close * (1000 + index), "f"),
                    "provider": PROVIDER_ID,
                    "providerRevision": "dsa-backtest-v2-fixture-1",
                    "freshness": "delayed",
                    "quality": "complete",
                    "completeness": "complete",
                }
            )
            index += 1
            cursor += timedelta(minutes=1)
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


def _fixture_fund_holdings(symbol: str) -> dict[str, Any]:
    canonical = _canonical_fund_symbol(symbol)
    holdings = [
        {"symbol": "600519.SH", "name": "贵州茅台", "weight": 0.08},
        {"symbol": "000001.SZ", "name": "平安银行", "weight": 0.06},
    ]
    evidence = hashlib.sha256(
        json.dumps(holdings, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "version": 1,
        "fundSymbol": canonical,
        "reportPeriod": "2024-Q4",
        "disclosureDate": _fixture_timestamp(),
        "provider": PROVIDER_ID,
        "fetchedAt": _fixture_timestamp(),
        "evidenceVersion": evidence,
        "holdings": holdings,
    }


def _normalize_fx_currency(value: str, field: str = "currency") -> str:
    normalized = value.strip().upper()
    if normalized not in FX_CURRENCIES:
        _error("invalid_currency", f"{field} 不支持: {value}", 422)
    return normalized


def _parse_fx_as_of(value: Optional[str]) -> date:
    if not value:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        _error("invalid_as_of", f"asOf 不是有效日期: {value}", 422)


def _fx_row(
    *,
    from_currency: str,
    to_currency: str,
    rate: float,
    rate_date: date,
    provider: str,
    fetched_at: str,
    stale: bool,
    as_of: date,
) -> dict[str, Any]:
    age_days = max(0, (as_of - rate_date).days)
    effective_stale = stale or age_days > 0
    return {
        "fromCurrency": from_currency,
        "toCurrency": to_currency,
        "rate": rate,
        "rateDate": rate_date.isoformat(),
        "provider": provider,
        "fetchedAt": fetched_at,
        "freshness": "stale" if effective_stale else "delayed",
        "stale": effective_stale,
        "ageDays": age_days,
        "available": age_days <= FX_MAX_AGE_DAYS,
    }


def _fixture_fx_rates(
    base_currency: str,
    currencies: list[str],
    as_of: date,
) -> dict[str, Any]:
    rates_to_cny = {"HKD": 0.92, "USD": 7.2}
    rows: list[dict[str, Any]] = []
    for currency in currencies:
        if currency == base_currency:
            rate = 1.0
        elif currency == "CNY" and base_currency in rates_to_cny:
            rate = 1 / rates_to_cny[base_currency]
        elif base_currency == "CNY" and currency in rates_to_cny:
            rate = rates_to_cny[currency]
        elif currency in rates_to_cny and base_currency in rates_to_cny:
            rate = rates_to_cny[currency] / rates_to_cny[base_currency]
        else:
            rate = 0.0
        if rate > 0:
            rows.append(
                _fx_row(
                    from_currency=currency,
                    to_currency=base_currency,
                    rate=rate,
                    rate_date=as_of,
                    provider="fixture",
                    fetched_at=_fixture_timestamp(),
                    stale=False,
                    as_of=as_of,
                )
            )
        else:
            rows.append(
                {
                    "fromCurrency": currency,
                    "toCurrency": base_currency,
                    "freshness": "unavailable",
                    "stale": False,
                    "ageDays": None,
                    "available": False,
                }
            )
    return {
        "version": 1,
        "baseCurrency": base_currency,
        "asOf": as_of.isoformat(),
        "fetchedAt": _fixture_timestamp(),
        "maxAgeDays": FX_MAX_AGE_DAYS,
        "rates": rows,
    }


def _cached_fx_rate(
    *,
    repo: Any,
    from_currency: str,
    to_currency: str,
    as_of: date,
) -> Optional[dict[str, Any]]:
    direct = repo.get_latest_fx_rate(
        from_currency=from_currency,
        to_currency=to_currency,
        as_of=as_of,
    )
    if direct is not None and float(direct.rate or 0) > 0:
        return {
            "rate": float(direct.rate),
            "rate_date": direct.rate_date,
            "provider": direct.source or "cache",
            "fetched_at": direct.updated_at.isoformat() if direct.updated_at else _now_iso(),
            "stale": bool(direct.is_stale),
        }
    inverse = repo.get_latest_fx_rate(
        from_currency=to_currency,
        to_currency=from_currency,
        as_of=as_of,
    )
    if inverse is not None and float(inverse.rate or 0) > 0:
        return {
            "rate": 1 / float(inverse.rate),
            "rate_date": inverse.rate_date,
            "provider": inverse.source or "cache",
            "fetched_at": inverse.updated_at.isoformat() if inverse.updated_at else _now_iso(),
            "stale": bool(inverse.is_stale),
        }
    return None


def _real_fx_rates(base_currency: str, currencies: list[str], as_of: date) -> dict[str, Any]:
    from src.services.portfolio_service import PortfolioService

    service = PortfolioService()
    rows: list[dict[str, Any]] = []
    for currency in currencies:
        if currency == base_currency:
            rows.append(
                _fx_row(
                    from_currency=currency,
                    to_currency=base_currency,
                    rate=1.0,
                    rate_date=as_of,
                    provider="identity",
                    fetched_at=_now_iso(),
                    stale=False,
                    as_of=as_of,
                )
            )
            continue
        cached = _cached_fx_rate(
            repo=service.repo,
            from_currency=currency,
            to_currency=base_currency,
            as_of=as_of,
        )
        if cached is None or (as_of - cached["rate_date"]).days > FX_MAX_AGE_DAYS:
            try:
                fetched = service._fetch_fx_rate_from_yfinance(
                    from_currency=currency,
                    to_currency=base_currency,
                    as_of_date=as_of,
                )
            except Exception:  # noqa: BLE001 - contract returns unavailable status.
                fetched = None
            if fetched is not None and fetched > 0:
                service.repo.save_fx_rate(
                    from_currency=currency,
                    to_currency=base_currency,
                    rate_date=as_of,
                    rate=fetched,
                    source="yfinance",
                    is_stale=False,
                )
                cached = {
                    "rate": float(fetched),
                    "rate_date": as_of,
                    "provider": "yfinance",
                    "fetched_at": _now_iso(),
                    "stale": False,
                }
        if cached is None or (as_of - cached["rate_date"]).days > FX_MAX_AGE_DAYS:
            rows.append(
                {
                    "fromCurrency": currency,
                    "toCurrency": base_currency,
                    "freshness": "unavailable",
                    "stale": False,
                    "ageDays": None if cached is None else (as_of - cached["rate_date"]).days,
                    "available": False,
                }
            )
            continue
        rows.append(
            _fx_row(
                from_currency=currency,
                to_currency=base_currency,
                rate=cached["rate"],
                rate_date=cached["rate_date"],
                provider=cached["provider"],
                fetched_at=cached["fetched_at"],
                stale=cached["stale"],
                as_of=as_of,
            )
        )
    return {
        "version": 1,
        "baseCurrency": base_currency,
        "asOf": as_of.isoformat(),
        "fetchedAt": _now_iso(),
        "maxAgeDays": FX_MAX_AGE_DAYS,
        "rates": rows,
    }


def _real_fund_nav(symbol: str, request_id: str | None = None) -> dict[str, Any]:
    canonical = _canonical_fund_symbol(symbol)
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        gateway_result = get_thesis_ledger_data_gateway().fund_nav(
            canonical,
            request_id=request_id,
        )
        frame = gateway_result.data
        provider = gateway_result.provider
        fallback_used = gateway_result.fallback_used
        fetched_at = _now_iso()
    except Exception as exc:  # noqa: BLE001 - gateway maps provider failures to diagnostics.
        _data_gateway_error(
            exc,
            "基金单位净值暂时不可用",
            no_eligible_message="当前策略没有可用的基金净值 Provider",
        )
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
    request_id: str | None = None,
) -> list[dict[str, Any]]:
    canonical = _canonical_fund_symbol(symbol)
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        gateway_result = get_thesis_ledger_data_gateway().fund_nav_history(
            canonical,
            start=start,
            end=end,
            limit=limit,
            request_id=request_id,
        )
        frame = gateway_result.data
        provider = gateway_result.provider
        fallback_used = gateway_result.fallback_used
    except Exception as exc:  # noqa: BLE001 - gateway exposes stable error codes.
        _data_gateway_error(
            exc,
            "基金净值历史暂时不可用",
            no_eligible_message="当前策略没有可用的基金净值历史 Provider",
        )

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

def _real_quote(symbol: str, request_id: str | None = None) -> dict[str, Any]:
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        gateway_result = get_thesis_ledger_data_gateway().quote(
            symbol,
            request_id=request_id,
        )
        quote = gateway_result.data
        provider = gateway_result.provider
        fallback_used = gateway_result.fallback_used
    except Exception as exc:  # noqa: BLE001 - gateway maps provider failures to diagnostics.
        _data_gateway_error(
            exc,
            "实时行情暂时不可用",
            no_eligible_message="当前策略没有可用的实时行情 Provider",
        )

    fetched_at = _iso_timestamp(getattr(quote, "fetched_at", None) or _now_iso())
    provider_timestamp = getattr(quote, "provider_timestamp", None)
    market_time = _iso_timestamp(provider_timestamp or fetched_at)
    stale = bool(getattr(quote, "is_stale", False))
    upstream_source = _upstream_source(getattr(quote, "source", None))
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
    result = {
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
    if upstream_source:
        result["upstreamSource"] = upstream_source
    return result


def _real_bars(
    symbol: str,
    start: Optional[str],
    end: Optional[str],
    limit: int,
    request_id: str | None = None,
    *,
    v2_raw: bool = False,
) -> list[dict[str, Any]]:
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        gateway_result = get_thesis_ledger_data_gateway().bars(
            symbol,
            timeframe="1d",
            start=start,
            end=end,
            limit=limit,
            request_id=request_id,
            parameters={"priceMode": "raw"} if v2_raw else {},
        )
        frame = gateway_result.data
        provider = gateway_result.provider
        fallback_used = gateway_result.fallback_used
        fetched_at = _now_iso() if v2_raw else None
    except Exception as exc:  # noqa: BLE001 - gateway maps provider failures to diagnostics.
        _data_gateway_error(
            exc,
            "日线数据暂时不可用",
            no_eligible_message="当前策略没有可用的日线 Provider",
        )
    if frame is None or frame.empty:
        _error("upstream_unavailable", f"没有 {symbol} 的日线数据", 503)
    upstream_source = _upstream_source(getattr(frame, "attrs", {}).get("upstream_source"))
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
        item = {
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
        if fetched_at is not None:
            item["fetchedAt"] = fetched_at
        if upstream_source:
            item["upstreamSource"] = upstream_source
        result.append(item)
    result.sort(key=lambda item: item["timestamp"])
    return result[-limit:]


def _v2_session_close_available_at(occurred_at: str, fetched_at: str | None) -> str | None:
    """Use CN cash-session close only after the fact is safely observable."""
    occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    local_date = occurred.astimezone(ZoneInfo("Asia/Shanghai")).date()
    session_close = datetime.combine(
        local_date,
        time(15, 0),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc)
    fetched = datetime.fromisoformat(
        _iso_timestamp(fetched_at or _now_iso()).replace("Z", "+00:00")
    )
    if occurred > fetched:
        return None
    if fetched < session_close:
        # Do not expose a partial current-day bar to a backtest.
        if fetched.astimezone(ZoneInfo("Asia/Shanghai")).date() == local_date:
            return None
    return session_close.isoformat()


def _v2_session_open_at(occurred_at: str) -> str:
    """Return the CN cash-session opening instant for a daily bar."""
    occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
    local_date = occurred.astimezone(ZoneInfo("Asia/Shanghai")).date()
    return datetime.combine(
        local_date,
        time(9, 30),
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).astimezone(timezone.utc).isoformat()


def _real_indicator(
    symbol: str,
    name: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    normalized = name.upper()
    if normalized not in {"MA", "MACD", "RSI"}:
        _error("unsupported_capability", f"指标 {normalized} 在 Contract V1 不可用", 422)
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        gateway_result = get_thesis_ledger_data_gateway().bars(
            symbol,
            timeframe="1d",
            limit=90,
            request_id=request_id,
        )
        frame = gateway_result.data
        provider = gateway_result.provider
        fallback_used = gateway_result.fallback_used
    except Exception as exc:  # noqa: BLE001 - gateway maps provider failures to diagnostics.
        _data_gateway_error(
            exc,
            "指标输入日线暂时不可用",
            no_eligible_message="当前策略没有可用的指标输入日线 Provider",
        )
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
        "provider": provider,
        "fallbackUsed": fallback_used,
        "engineVersion": ENGINE_VERSION,
    }


def _ratio(value: Any, field: str) -> float:
    result = _number(value, field)
    if result > 1:
        result /= 100
    if not 0 <= result <= 1:
        _error("upstream_invalid_response", f"字段 {field} 不在 0 到 1 之间", 502)
    return result


def _fund_holdings_payload(
    symbol: str,
    frame: Any,
    provider: str,
    fallback_used: bool,
) -> dict[str, Any]:
    canonical = _canonical_fund_symbol(symbol)
    if frame is None or getattr(frame, "empty", True):
        _error("upstream_unavailable", f"没有 {canonical} 的基金持仓披露", 503)
    required = {"股票代码", "股票名称", "占净值比例", "季度"}
    if not required.issubset(set(frame.columns)):
        _error("upstream_invalid_response", "基金持仓披露缺少必要字段", 502)

    quarter_rows: list[tuple[tuple[int, int], Any]] = []
    for _, row in frame.iterrows():
        match = re.search(r"(\d{4})年\s*([1-4])季度", str(row["季度"]))
        if match:
            quarter_rows.append(((int(match.group(1)), int(match.group(2))), row))
    if not quarter_rows:
        _error("upstream_invalid_response", "基金持仓披露缺少可识别的报告期", 502)
    latest = max(key for key, _ in quarter_rows)
    aggregate: dict[str, dict[str, Any]] = {}
    for key, row in quarter_rows:
        if key != latest:
            continue
        holding_symbol = _canonical_symbol(str(row["股票代码"]))
        weight = _number(row["占净值比例"], "weight") / 100
        current = aggregate.get(holding_symbol)
        if current is None:
            aggregate[holding_symbol] = {
                "symbol": holding_symbol,
                "name": str(row["股票名称"]).strip(),
                "weight": weight,
            }
        else:
            current["weight"] = float(current["weight"]) + weight
    holdings = sorted(aggregate.values(), key=lambda row: (-float(row["weight"]), row["symbol"]))
    if not holdings or sum(float(row["weight"]) for row in holdings) > 1.000001:
        _error("upstream_invalid_response", "基金持仓披露权重非法", 502)
    fetched_at = _now_iso()
    evidence = hashlib.sha256(
        json.dumps(holdings, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "version": 1,
        "fundSymbol": canonical,
        "reportPeriod": f"{latest[0]}-Q{latest[1]}",
        "disclosureDate": fetched_at,
        "provider": provider,
        "fetchedAt": fetched_at,
        "evidenceVersion": evidence,
        "fallbackUsed": fallback_used,
        "holdings": holdings,
    }


def _real_fund_holdings(symbol: str, request_id: str | None = None) -> dict[str, Any]:
    canonical = _canonical_fund_symbol(symbol)
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        result = get_thesis_ledger_data_gateway().fund_holdings(
            canonical,
            request_id=request_id or _request_id_context.get(),
        )
    except Exception as exc:  # noqa: BLE001 - gateway exposes stable error codes.
        _data_gateway_error(
            exc,
            "基金持仓披露暂时不可用",
            no_eligible_message="当前策略没有可用的基金持仓 Provider",
        )
    return _fund_holdings_payload(canonical, result.data, result.provider, result.fallback_used)


def _real_chip(symbol: str, request_id: str | None = None) -> dict[str, Any]:
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        gateway_result = get_thesis_ledger_data_gateway().chip_summary(
            symbol,
            request_id=request_id,
        )
        chip = gateway_result.data
        provider = gateway_result.provider
        fallback_used = gateway_result.fallback_used
    except Exception as exc:  # noqa: BLE001 - gateway maps provider failures to diagnostics.
        _data_gateway_error(
            exc,
            "筹码摘要暂时不可用",
            no_eligible_message="当前策略没有可用的筹码摘要 Provider",
        )
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
        "provider": provider,
        "fallbackUsed": fallback_used,
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
            "fund-holdings": {"assetSuffix": ".OF", "capability": "FUND_HOLDINGS"},
            "fx-rates": {"currencies": sorted(FX_CURRENCIES), "maxAgeDays": FX_MAX_AGE_DAYS},
            "bars": {"timeframes": ["1d"]},
            "indicators": {
                "names": ["MA", "MACD", "RSI"],
                "timeframes": ["1d"],
                "inputCapability": "DAILY_BAR",
            },
            "chip": {"summary": True, "capability": "CHIP_SUMMARY", "distribution": False},
            "catalog": {"snapshot": True, "delta": True},
        },
        "unsupported": ["bars:1m", "indicator:ATR", "chip:distribution"],
        "backtestData": _backtest_capabilities(),
    }


@router.get("/v2/capabilities", dependencies=[Depends(require_contract_token)])
def backtest_capabilities() -> dict[str, Any]:
    """V2 data facts/capability contract owned by DSA."""
    return _backtest_capabilities()


@router.get("/market/fx-rates", dependencies=[Depends(require_contract_token)])
def fx_rates(
    base_currency: str = Query("CNY", alias="baseCurrency"),
    currencies: str = Query("", description="Comma-separated source currencies"),
    as_of: Optional[str] = Query(default=None, alias="asOf"),
) -> dict[str, Any]:
    base = _normalize_fx_currency(base_currency, "baseCurrency")
    requested = sorted(
        {
            _normalize_fx_currency(item, "currencies")
            for item in currencies.split(",")
            if item.strip()
        }
    )
    if base not in requested:
        requested.insert(0, base)
    date_value = _parse_fx_as_of(as_of)
    return (
        _fixture_fx_rates(base, requested, date_value)
        if _fixture_mode()
        else _real_fx_rates(base, requested, date_value)
    )


@router.get("/market/fund-nav", dependencies=[Depends(require_contract_token)])
def fund_nav(
    request: Request,
    symbol: str = Query(..., min_length=1),
) -> dict[str, Any]:
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return (
        _fixture_fund_nav(symbol)
        if _fixture_mode()
        else _real_fund_nav(symbol, request_id=request_id)
    )


@router.get("/market/fund-nav/history", dependencies=[Depends(require_contract_token)])
def fund_nav_history(
    request: Request,
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
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return _real_fund_nav_history(symbol, start, end, limit, request_id=request_id)


@router.get("/market/fund-holdings", dependencies=[Depends(require_contract_token)])
def fund_holdings(
    request: Request,
    symbol: str = Query(..., min_length=1),
) -> dict[str, Any]:
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return (
        _fixture_fund_holdings(symbol)
        if _fixture_mode()
        else _real_fund_holdings(symbol, request_id=request_id)
    )


@router.get("/market/quote", dependencies=[Depends(require_contract_token)])
def quote(
    request: Request,
    symbol: str = Query(..., min_length=1),
) -> dict[str, Any]:
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return (
        _fixture_quote(symbol)
        if _fixture_mode()
        else _real_quote(symbol, request_id=request_id)
    )


@router.get("/market/bars", dependencies=[Depends(require_contract_token)])
def bars(
    request: Request,
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
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return _real_bars(symbol, start, end, limit, request_id=request_id)


@router.get("/v2/market/bars", dependencies=[Depends(require_contract_token)])
def backtest_bars(
    request: Request,
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("1d"),
    start: Optional[str] = Query(default=None),
    end: Optional[str] = Query(default=None),
    limit: int = Query(90, ge=1, le=10000),
) -> list[dict[str, Any]]:
    if timeframe not in {"1m", "1d"}:
        _error("unsupported_capability", "DSA V2 基础 Bar 只支持 1m/1d", 422)
    if _fixture_mode():
        result = _fixture_minute_bars(symbol) if timeframe == "1m" else [
            {
                **item,
                "market": _backtest_market_for_symbol(symbol),
                "occurredAt": item["timestamp"],
                "availableAt": item["timestamp"],
                "openedAt": _v2_session_open_at(item["timestamp"]),
                "openAvailableAt": _v2_session_open_at(item["timestamp"]),
                "open": _decimal_string(item["open"], "open"),
                "high": _decimal_string(item["high"], "high"),
                "low": _decimal_string(item["low"], "low"),
                "close": _decimal_string(item["close"], "close"),
                "volume": _decimal_string(item["volume"], "volume"),
                "amount": _decimal_string(item["amount"], "amount"),
                "providerRevision": "dsa-backtest-v2-fixture-1",
                "quality": "complete",
                "completeness": "complete",
            }
            for item in _fixture_bars(symbol)
        ]
        return [
            item
            for item in result
            if (not start or item["timestamp"] >= start)
            and (not end or item["timestamp"] <= end)
        ][-limit:]
    if timeframe == "1d":
        request_id = request.headers.get("x-request-id") or _request_id_context.get()
        result = _real_bars(
            symbol,
            start,
            end,
            limit,
            request_id=request_id,
            v2_raw=True,
        )
        rows: list[dict[str, Any]] = []
        for item in result:
            available_at = _v2_session_close_available_at(
                item["timestamp"], item.get("fetchedAt")
            )
            if available_at is None:
                continue
            rows.append(
                {
                    **item,
                    "market": _backtest_market_for_symbol(symbol),
                    "occurredAt": item["timestamp"],
                    "availableAt": available_at,
                    "openedAt": _v2_session_open_at(item["timestamp"]),
                    "openAvailableAt": _v2_session_open_at(item["timestamp"]),
                    "open": _decimal_string(item["open"], "open"),
                    "high": _decimal_string(item["high"], "high"),
                    "low": _decimal_string(item["low"], "low"),
                    "close": _decimal_string(item["close"], "close"),
                    "volume": _decimal_string(item["volume"], "volume"),
                    "amount": _decimal_string(item["amount"], "amount"),
                    "providerRevision": "dsa-backtest-v2-provider-raw-1",
                    "quality": "complete",
                    "completeness": "complete",
                }
            )
        if not rows:
            _error("upstream_unavailable", "没有已完成 CN 交易日收盘的原始日线", 503)
        return rows
    _error("upstream_unavailable", "Provider 当前没有可用的 1m Bar 数据", 503)


@router.get("/v2/calendar", dependencies=[Depends(require_contract_token)])
def v2_calendar(
    start: str = Query(..., min_length=10, max_length=10),
    end: str = Query(..., min_length=10, max_length=10),
    dataAsOf: str = Query(..., min_length=20),
    market: str = Query(..., min_length=2, max_length=2),
) -> dict[str, Any]:
    if market != "CN":
        _error("unsupported_capability", "V2 当前仅支持 CN 市场交易日历", 422)
    try:
        data_as_of = parse_data_as_of(dataAsOf)
        start_date, end_date = validate_range(start, end, data_as_of)
    except V2DependencyError as exc:
        _error(exc.code, str(exc), exc.status_code)
    if _fixture_mode():
        fact = fixture_calendar(start, end, data_as_of, _backtest_capabilities()["calendars"])
    else:
        fact = calendar_fact(start_date, end_date, data_as_of)
    if fact is None:
        return v2_response(
            status="unavailable",
            provider="exchange-calendars",
            provider_revision="exchange-calendars-unavailable",
            coverage={"start": start, "end": end, "complete": False},
            facts=[],
            reason="CN 交易日历 Provider 当前不可用或未确认覆盖",
        )
    return v2_response(
        status="supported",
        provider=str(fact["provider"]),
        provider_revision=str(fact["providerRevision"]),
        coverage={"start": start, "end": end, "complete": True},
        facts=[fact],
    )


@router.get("/v2/instrument-facts", dependencies=[Depends(require_contract_token)])
def v2_instrument_facts(
    symbol: str = Query(..., min_length=1),
    market: str = Query(..., min_length=2, max_length=2),
    instrumentType: str = Query(..., min_length=1),
    dataAsOf: str = Query(..., min_length=20),
) -> dict[str, Any]:
    try:
        canonical = validate_cn_stock(symbol, market, instrumentType, _canonical_symbol)
        data_as_of = parse_data_as_of(dataAsOf)
    except V2DependencyError as exc:
        _error(exc.code, str(exc), exc.status_code)
    if _fixture_mode():
        facts = [
            {
                **fact,
                "symbol": canonical,
                "availableAt": data_as_of.isoformat(),
                "occurredAt": data_as_of.isoformat(),
            }
            for fact in _backtest_capabilities()["instrumentFacts"]
            if fact["symbol"] == canonical
        ]
        if facts:
            return v2_response(
                status="supported",
                provider=PROVIDER_ID,
                provider_revision="instrument-v2-fixture-1",
                coverage={"start": None, "end": None, "complete": True},
                facts=facts,
            )
    return v2_response(
        status="supported",
        provider="dsa-market-rules",
        provider_revision="cn-a-share-standard-lot-tick-v1",
        coverage={"start": None, "end": None, "complete": True},
        facts=[static_cn_instrument_fact(canonical, data_as_of)],
    )


@router.get("/v2/corporate-actions", dependencies=[Depends(require_contract_token)])
def v2_corporate_actions(
    symbol: str = Query(..., min_length=1),
    market: str = Query(..., min_length=2, max_length=2),
    instrumentType: str = Query(..., min_length=1),
    start: str = Query(..., min_length=10, max_length=10),
    end: str = Query(..., min_length=10, max_length=10),
    dataAsOf: str = Query(..., min_length=20),
) -> dict[str, Any]:
    try:
        canonical = validate_cn_stock(symbol, market, instrumentType, _canonical_symbol)
        data_as_of = parse_data_as_of(dataAsOf)
        start_date, end_date = validate_range(start, end, data_as_of)
    except V2DependencyError as exc:
        _error(exc.code, str(exc), exc.status_code)
    if _fixture_mode():
        fixture_facts = [
            {
                **fact,
                "symbol": canonical,
                "market": "CN",
                "instrumentType": "STOCK",
            }
            for fact in _backtest_capabilities()["corporateActions"] ["facts"]
            if fact.get("symbol") == canonical
        ]
        return v2_response(
            status="supported" if fixture_facts else "unavailable",
            provider=PROVIDER_ID,
            provider_revision="corporate-action-v2-fixture-1",
            coverage={"start": start, "end": end, "complete": bool(fixture_facts)},
            facts=fixture_facts,
            reason=None if fixture_facts else "fixture 未覆盖该标的的公司行动",
        )
    return real_corporate_actions(
        canonical,
        start_date.isoformat(),
        end_date.isoformat(),
        data_as_of,
    )


@router.get("/market/indicators/{name}", dependencies=[Depends(require_contract_token)])
def indicator(
    name: str,
    request: Request,
    symbol: str = Query(..., min_length=1),
    timeframe: str = Query("1d"),
) -> dict[str, Any]:
    if timeframe != "1d":
        _error("unsupported_capability", "Contract V1 只支持 1d indicators", 422)
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return (
        _fixture_indicator(symbol, name)
        if _fixture_mode()
        else _real_indicator(symbol, name, request_id=request_id)
    )


@router.get("/market/chip", dependencies=[Depends(require_contract_token)])
def chip(request: Request, symbol: str = Query(..., min_length=1)) -> dict[str, Any]:
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return (
        _fixture_chip(symbol)
        if _fixture_mode()
        else _real_chip(symbol, request_id=request_id)
    )


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
                        elif capability == "FUND_HOLDINGS":
                            _fixture_fund_holdings("000001.OF")
                        elif capability == "CHIP_SUMMARY":
                            _fixture_chip("600519.SH")
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


@router.get("/control/catalog/jobs/{job_id}", dependencies=[Depends(require_control_token)])
def control_catalog_job_status(job_id: str) -> dict[str, Any]:
    try:
        return _control_store().get_catalog_job(job_id)
    except ControlContractError as error:
        _control_http_error(error)


@router.post("/control/catalog/ack", dependencies=[Depends(require_control_token)])
def control_catalog_ack(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        _control_envelope(payload)
        return _control_store().catalog_ack(payload)
    except ControlContractError as error:
        _control_http_error(error)
