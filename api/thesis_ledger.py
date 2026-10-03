# -*- coding: utf-8 -*-
"""ThesisLedger 当前数据与控制 API。

该模块只负责把 DSA 的原生数据能力映射成 ThesisLedger 的稳定 HTTP 契约，
不改变 DSA 现有原生路由。契约使用独立 Bearer Token，避免复用管理员会话。
"""

from __future__ import annotations

import math
import os
import logging
import hashlib
import json
import uuid
import re
from decimal import Decimal
from contextvars import ContextVar
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from api.thesis_ledger_source_basis import native_field_units, native_source_basis
from api.thesis_ledger_source_identity import upstream_source as _upstream_source

from src.services.thesis_ledger_control import (
    CONSUMER_NAMESPACE,
    CONTROL_CONTRACT_V3_VERSION,
    ControlContractError,
    ThesisLedgerControlStore,
)
from src.services.provider_credentials import CredentialValue, merge_credential_patch, parse_credential_patch
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_dependency_facts import (
    DependencyFactError,
    calendar_fact,
    fixture_calendar,
    parse_data_as_of,
    response as dependency_response,
    instrument_facts_response,
    validate_cn_stock,
    validate_calendar_range,
)
from src.services.thesis_ledger_market_v3_facts import (
    canonical_market_coverage_proof_v3,
    market_calendar_evidence_v3,
    market_coverage_context_v3,
)

CONTRACT_VERSION = 3
PROVIDER_ID = "akshare"
ENGINE_VERSION = "dsa-thesis-ledger-v1"
LOCAL_FIXTURE_VERSION = "dsa-thesis-ledger-fixture-v1"
MARKET_DATA_V3_METHOD_VERSION = "dsa-market-bars-v3-native-source-v1"
MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS = frozenset({"akshare", "tencent", "hithink", "tushare"})
FX_CURRENCIES = {"CNY", "HKD", "USD"}
FX_MAX_AGE_DAYS = 7
_request_id_context: ContextVar[str | None] = ContextVar("thesis_ledger_request_id", default=None)
logger = logging.getLogger(__name__)

router_v3 = APIRouter(
    prefix="/thesis-ledger",
    tags=["ThesisLedger Market Contract V3"],
)

_MARKET_DATA_V3_ERROR_MESSAGES = {
    "unsupported_data_contract_version": "不支持请求的数据契约版本",
    "unsupported_price_basis": "来源不支持请求的价格口径",
    "insufficient_coverage": "请求区间的数据覆盖不足",
    "upstream_failure": "行情上游暂时不可用",
    "invalid_response": "行情上游响应格式无效",
}


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


def _fixture_cn_calendar_sources() -> list[dict[str, Any]]:
    return [{
        "market": "CN", "timezone": "Asia/Shanghai",
        "provider": "exchange-calendar",
        "providerRevision": "fixture-calendar-2025-01-10",
        "sessions": [
            {"startMinute": 570, "endMinute": 690},
            {"startMinute": 780, "endMinute": 900},
        ],
        "holidays": [],
        "range": {"start": "2025-01-10", "end": "2025-01-10"},
    }]


def _fixture_cn_instrument_facts(symbol: str, instrument_type: str) -> list[dict[str, Any]]:
    if symbol != "600519.SH" or instrument_type != "STOCK":
        return []
    known_at = "2025-01-09T07:00:00+00:00"
    return [{
        "symbol": symbol, "market": "CN", "instrumentType": "STOCK",
        "currency": "CNY", "lotSize": "100", "tickSize": "0.01",
        "tradable": True,
        "executionRules": {
            "status": "supported", "version": "market-rules-v1",
            "range": {"start": "2024-11-12", "end": "2025-01-10"},
            "price": {"reference": "previousClose", "maxUpRatio": "0.1",
                      "maxDownRatio": "0.1"},
            "positionSettlement": {"sellableAfterTradingDays": 1},
            "cashSettlement": {"buyDebitAfterTradingDays": 0,
                               "sellCreditAfterTradingDays": 1},
            "statutoryCharges": [],
        },
        "provider": PROVIDER_ID,
        "providerRevision": "instrument-fixture-1",
        "occurredAt": known_at,
        "availableAt": known_at,
    }]


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


def _fixture_quote(symbol: str) -> dict[str, Any]:
    canonical = _canonical_symbol(symbol)
    price = _fixture_price(canonical)
    now = _fixture_timestamp()
    return {
        "version": 3,
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


def _fixture_chip(symbol: str) -> dict[str, Any]:
    canonical = _canonical_symbol(symbol)
    price = _fixture_price(canonical)
    calculated_at = _fixture_timestamp()
    return {
        "version": 3,
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
        "version": 3,
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
        "version": 3,
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
        "version": 3,
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
        "version": 3,
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
    from src.services.thesis_ledger_nav_dates import nav_datetime

    latest = max((row for _, row in frame.iterrows()), key=lambda row: nav_datetime(row[date_column]))
    nav_date = _iso_timestamp(latest[date_column])
    unit_nav = _number(latest[nav_column], "unitNav", allow_zero=False)
    parsed_date = datetime.fromisoformat(nav_date.replace("Z", "+00:00"))
    age_days = max(0, (datetime.now(timezone.utc).date() - parsed_date.date()).days)
    freshness = "stale" if age_days > 7 else "delayed"
    return {
        "version": 3,
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
    for _, row in frame.iterrows():
        nav_date = _iso_timestamp(row[date_column])
        parsed = datetime.fromisoformat(nav_date.replace("Z", "+00:00"))
        if start_at and parsed < start_at:
            continue
        if end_at and parsed > end_at:
            continue
        age_days = max(0, (datetime.now(timezone.utc).date() - parsed.date()).days)
        result.append(
            {
                "version": 3,
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
    from api.thesis_ledger_quote_time import quote_time_state

    provider_timestamp, stale, freshness = quote_time_state(
        getattr(quote, "provider_timestamp", None), getattr(quote, "is_stale", None),
        datetime.now(timezone.utc),
    )
    market_time = _iso_timestamp(provider_timestamp) if provider_timestamp else None
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
        "version": 3,
        "symbol": _canonical_symbol(symbol),
        **values,
        "stale": stale,
        "provider": provider,
        "marketTime": market_time,
        "fetchedAt": fetched_at,
        "freshness": freshness,
        "fallbackUsed": fallback_used,
    }
    if upstream_source:
        result["upstreamSource"] = upstream_source
    if provider == "hithink":
        from api.thesis_ledger_quote_units import hithink_quote_public_units

        units = hithink_quote_public_units(upstream_source, getattr(quote, "units", None))
        if units is None:
            _error("upstream_invalid_response", "HiThink 报价单位合同无效", 502)
        result["units"] = units
    return result


def _canonical_number(value: Any) -> str | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("canonical fingerprint 只接受有限数字")
    raw = format(number, ".17g").lower()
    if "e" not in raw:
        return raw.rstrip("0").rstrip(".") if "." in raw else raw
    mantissa, exponent_text = raw.split("e", 1)
    exponent = int(exponent_text)
    sign = "-" if mantissa.startswith("-") else ""
    unsigned_mantissa = mantissa.lstrip("-")
    digits = unsigned_mantissa.replace(".", "")
    decimal_point = unsigned_mantissa.find(".")
    decimal_index = (decimal_point if decimal_point >= 0 else len(digits)) + exponent
    if decimal_index <= 0:
        shifted = f"0.{('0' * -decimal_index)}{digits}"
    elif decimal_index >= len(digits):
        shifted = f"{digits}{'0' * (decimal_index - len(digits))}"
    else:
        shifted = f"{digits[:decimal_index]}.{digits[decimal_index:]}"
    return f"{sign}{shifted}".rstrip("0").rstrip(".") if "." in shifted else f"{sign}{shifted}"


def _bar_series_fingerprint(
    points: list[dict[str, Any]],
    identity: Mapping[str, Any],
    *,
    coverage_proof: Mapping[str, Any] | None = None,
) -> str:
    canonical = [
        [
            point["timestamp"],
            *(_canonical_number(point[key]) for key in ("open", "high", "low", "close", "volume", "amount")),
            point["completionStatus"],
            point["availableAt"],
        ]
        for point in sorted(points, key=lambda item: item["timestamp"])
    ]
    payload = [
        [identity["symbol"], identity["assetType"], identity["timeframe"], identity["adjustment"]],
        canonical,
    ]
    if coverage_proof is not None:
        payload.append(canonical_market_coverage_proof_v3(coverage_proof))
    if "fieldUnits" in identity:
        payload.append(["fieldUnits", identity["fieldUnits"]["volume"], identity["fieldUnits"]["amount"]])
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _bar_series_point(
    item: Mapping[str, Any], *, fetched_at: str, observed_availability: bool = False,
) -> dict[str, Any]:
    session_close = _session_close_available_at(item["timestamp"], fetched_at)
    return {
        "timestamp": item["timestamp"],
        "open": item["open"],
        "high": item["high"],
        "low": item["low"],
        "close": item["close"],
        "volume": item["volume"],
        "amount": item["amount"],
        "completionStatus": "complete" if session_close else "incomplete",
        "availableAt": fetched_at if observed_availability else session_close or fetched_at,
    }


def _market_data_v3_error(code: str, status_code: int, request_id: str) -> None:
    message = _MARKET_DATA_V3_ERROR_MESSAGES[code]
    raise HTTPException(
        status_code=status_code,
        detail={
            "contractVersion": 3,
            "requestId": request_id,
            "error": {"code": code, "message": message},
        },
    )


def _market_data_v3_request(payload: Any, fallback_request_id: str) -> dict[str, Any]:
    value = payload if isinstance(payload, dict) else {}
    raw_request_id = value.get("requestId")
    request_id = (
        raw_request_id.strip()
        if isinstance(raw_request_id, str) and raw_request_id.strip()
        else fallback_request_id
    )
    version = value.get("contractVersion")
    if isinstance(version, bool) or version != 3:
        _market_data_v3_error("unsupported_data_contract_version", 422, request_id)
    required_fields = {"contractVersion", "requestId", "symbol", "routeKey", "start", "end"}
    if (
        not required_fields.issubset(value)
        or set(value) - required_fields - {"routeTarget", "tradabilityMode"}
    ):
        _market_data_v3_error("invalid_response", 422, request_id)
    symbol = value.get("symbol")
    route_key = value.get("routeKey")
    if not isinstance(symbol, str) or not symbol.strip() or not isinstance(route_key, dict):
        _market_data_v3_error("invalid_response", 422, request_id)
    symbol = symbol.strip()
    if not isinstance(raw_request_id, str) or not raw_request_id.strip():
        _market_data_v3_error("invalid_response", 422, request_id)
    if set(route_key) != {"kind", "market", "assetType", "capability", "timeframe", "adjustment"}:
        _market_data_v3_error("invalid_response", 422, request_id)
    if (
        route_key.get("kind") != "bar"
        or route_key.get("market") not in {"CN", "HK", "US"}
        or route_key.get("assetType") not in {
            "STOCK", "ETF", "MUTUAL_FUND", "LOF", "INDEX", "BOND", "CONVERTIBLE_BOND"
        }
        or route_key.get("capability") not in {"DAILY_BAR", "MINUTE_BAR"}
        or route_key.get("timeframe") not in {"1d", "1m"}
        or route_key.get("adjustment") not in {"none", "qfq", "hfq"}
        or (route_key["capability"] == "DAILY_BAR") != (route_key["timeframe"] == "1d")
    ):
        _market_data_v3_error("invalid_response", 422, request_id)
    start, end = value.get("start"), value.get("end")
    if not isinstance(start, str) or not isinstance(end, str):
        _market_data_v3_error("invalid_response", 422, request_id)
    try:
        if date.fromisoformat(start).isoformat() != start or date.fromisoformat(end).isoformat() != end:
            raise ValueError("non-canonical date")
    except ValueError:
        _market_data_v3_error("invalid_response", 422, request_id)
    if start > end:
        _market_data_v3_error("invalid_response", 422, request_id)
    if _backtest_market_for_symbol(symbol) != route_key["market"]:
        _market_data_v3_error("invalid_response", 422, request_id)
    from src.services.thesis_ledger_provider_runtime import instrument_type_for_symbol

    if instrument_type_for_symbol(symbol) != route_key["assetType"]:
        _market_data_v3_error("invalid_response", 422, request_id)
    route_target = None
    if "routeTarget" in value:
        raw_target = value["routeTarget"]
        if not isinstance(raw_target, dict) or set(raw_target) != {
            "providerId",
            "upstreamSource",
            "routeIndex",
        }:
            _market_data_v3_error("invalid_response", 422, request_id)
        provider_id = raw_target.get("providerId")
        upstream_source = raw_target.get("upstreamSource")
        route_index = raw_target.get("routeIndex")
        if (
            not isinstance(provider_id, str)
            or not provider_id.strip()
            or not isinstance(upstream_source, str)
            or not upstream_source.strip()
            or not isinstance(route_index, int)
            or isinstance(route_index, bool)
            or route_index not in {0, 1}
        ):
            _market_data_v3_error("invalid_response", 422, request_id)
        route_target = {
            "providerId": provider_id.strip(),
            "upstreamSource": upstream_source.strip(),
            "routeIndex": route_index,
        }

    request_data = {
        "contractVersion": 3,
        "requestId": request_id,
        "symbol": symbol,
        "routeKey": dict(route_key),
        "start": start,
        "end": end,
    }
    if route_target is not None:
        request_data["routeTarget"] = route_target
    if "tradabilityMode" in value:
        if value["tradabilityMode"] != "assume-untradable-no-bar" or route_key["market"] != "CN" or route_key["timeframe"] != "1d":
            _market_data_v3_error("invalid_response", 422, request_id)
        request_data["tradabilityMode"] = value["tradabilityMode"]
    return request_data


def _market_data_v3_coverage_context(request_data: Mapping[str, Any]) -> dict[str, Any] | None:
    """Resolve exact calendar and listing facts before any V3 Provider request."""
    return market_coverage_context_v3(
        symbol=str(request_data["symbol"]),
        market=str(request_data["routeKey"]["market"]),
        start=str(request_data["start"]),
        end=str(request_data["end"]),
        calendar_evidence_provider=market_calendar_evidence_v3,
    )


def _market_data_v3_pagination_proof(
    frame: Any,
    *,
    asset_type: str,
    provider: str,
    upstream_source: str,
    expected_session_count: int,
    requested_start: str,
    requested_end: str,
    request_id: str,
    requested_adjustment: str | None = None,
) -> dict[str, Any]:
    from src.services.thesis_ledger_market_v3_pagination import MarketPaginationError, market_pagination_proof_v3

    try:
        return market_pagination_proof_v3(
            frame, asset_type=asset_type, provider=provider, upstream_source=upstream_source,
            expected_session_count=expected_session_count,
            requested_start=requested_start, requested_end=requested_end,
            requested_adjustment=requested_adjustment,
        )
    except MarketPaginationError as error:
        _market_data_v3_error(error.code, 422 if error.code == "insufficient_coverage" else 502, request_id)


def _market_data_v3_response(
    request_data: Mapping[str, Any],
    execution: Any,
    coverage_context: Mapping[str, Any],
) -> dict[str, Any]:
    request_id = str(request_data["requestId"])
    route_key = request_data["routeKey"]
    expected_sessions = coverage_context.get("expectedPostListingSessionDates")
    if not isinstance(expected_sessions, list) or not expected_sessions:
        _market_data_v3_error("insufficient_coverage", 422, request_id)
    provider = str(getattr(execution, "provider", "") or "").strip().lower()
    upstream_source = str(getattr(execution, "upstream_source", "") or "").strip().lower()
    route_index = getattr(execution, "route_index", None)
    policy_revision = getattr(execution, "effective_revision", None)
    if (
        provider not in MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS
        or not upstream_source
        or not MARKET_DATA_V3_METHOD_VERSION
        or not isinstance(route_index, int)
        or isinstance(route_index, bool)
        or route_index not in {0, 1}
        or not isinstance(policy_revision, int)
        or isinstance(policy_revision, bool)
        or policy_revision < 0
    ):
        _market_data_v3_error("invalid_response", 502, request_id)
    route_target = request_data.get("routeTarget")
    if isinstance(route_target, Mapping) and (
        provider != route_target.get("providerId")
        or upstream_source != route_target.get("upstreamSource")
        or route_index != route_target.get("routeIndex")
    ):
        _market_data_v3_error("invalid_response", 502, request_id)

    frame = getattr(execution, "value", None)
    pagination_proof = _market_data_v3_pagination_proof(
        frame,
        asset_type=route_key["assetType"],
        provider=provider,
        upstream_source=upstream_source,
        expected_session_count=len(expected_sessions),
        requested_start=str(request_data["start"]),
        requested_end=str(request_data["end"]),
        requested_adjustment=str(route_key["adjustment"]),
        request_id=request_id,
    )
    fetched_at = _now_iso()
    points: list[dict[str, Any]] = []
    trading_dates: list[str] = []
    try:
        for _, row in frame.iterrows():
            timestamp = _iso_timestamp(row.get("date"))
            trading_date = datetime.fromisoformat(timestamp).astimezone(
                ZoneInfo("Asia/Shanghai")
            ).date().isoformat()
            if not request_data["start"] <= trading_date <= request_data["end"]:
                _market_data_v3_error("invalid_response", 502, request_id)
            fields = {
                name: _number(row.get(name), name)
                for name in ("open", "high", "low", "close", "volume", "amount")
            }
            if any(number < 0 for number in fields.values()):
                _market_data_v3_error("invalid_response", 502, request_id)
            if fields["high"] < max(fields["open"], fields["close"], fields["low"]):
                _market_data_v3_error("invalid_response", 502, request_id)
            if fields["low"] > min(fields["open"], fields["close"], fields["high"]):
                _market_data_v3_error("invalid_response", 502, request_id)
            item = _bar_series_point(
                {"timestamp": timestamp, **fields}, fetched_at=fetched_at, observed_availability=True,
            )
            points.append(item)
            trading_dates.append(trading_date)
    except HTTPException:
        raise
    except Exception:
        _market_data_v3_error("invalid_response", 502, request_id)
    points.sort(key=lambda item: item["timestamp"])
    from src.services.thesis_ledger_market_daily_tradability import market_daily_tradability
    try:
        daily_windows = market_daily_tradability(request_data, execution, coverage_context, trading_dates, fetched_at)
    except Exception as error:
        _market_data_v3_error(getattr(error, "code", "invalid_response"), 422, request_id)
    if any(point["completionStatus"] != "complete" for point in points):
        _market_data_v3_error("insufficient_coverage", 422, request_id)
    attrs = getattr(frame, "attrs", {})
    try:
        units = native_field_units(attrs, route_key, provider, upstream_source, request_data)
    except (ValueError, TypeError, KeyError):
        _market_data_v3_error("invalid_response", 502, request_id)
    has_more_before = attrs.get("has_more_before", False) if isinstance(attrs, Mapping) else False
    if not isinstance(has_more_before, bool):
        _market_data_v3_error("invalid_response", 502, request_id)
    coverage_proof = {
        "calendar": dict(coverage_context["calendar"]),
        "listing": dict(coverage_context["listing"]),
        "window": dict(coverage_context["window"]),
        "pagination": pagination_proof,
    }
    fingerprint = _bar_series_fingerprint(
        points,
        {
            "symbol": request_data["symbol"],
            "assetType": route_key["assetType"],
            "timeframe": route_key["timeframe"],
            "adjustment": route_key["adjustment"],
            **units,
        },
        coverage_proof=coverage_proof,
    )
    source_basis = native_source_basis(
        route_key["adjustment"], MARKET_DATA_V3_METHOD_VERSION, fingerprint, fetched_at, units,
    )
    response = {
        "contractVersion": 3,
        "requestId": request_id,
        "symbol": request_data["symbol"],
        "routeKey": dict(route_key),
        "bars": points,
        "coverageProof": coverage_proof,
        "coverage": {
            "requestedStart": request_data["start"],
            "requestedEnd": request_data["end"],
            "actualStart": points[0]["timestamp"] if points else None,
            "actualEnd": points[-1]["timestamp"] if points else None,
            "hasMoreBefore": has_more_before,
            "latestCompleteTradingDate": max(trading_dates) if trading_dates else None,
        },
        "sourcePriceBasis": source_basis,
        "provenance": {
            "providerId": provider,
            "upstreamSource": upstream_source,
            "routeIndex": route_index,
            "effectivePolicyRevision": policy_revision,
        },
        "inputFingerprint": fingerprint,
    }
    return {**response, **({"historicalTradabilityWindows": daily_windows} if daily_windows is not None else {})}


@router_v3.get("/capabilities")
def market_data_capabilities_v3() -> dict[str, Any]:
    """Report Data contract support independently from the Control handshake."""
    from src.services.thesis_ledger_multi_window_fingerprint import MULTI_WINDOW_PROTOCOL
    return {
        "dataContractVersions": [3],
        "serviceCapabilities": {"fundNav": True},
        "multiWindowProtocols": [MULTI_WINDOW_PROTOCOL],
    }


@router_v3.post(
    "/market/bars",
    dependencies=[Depends(require_contract_token)],
    response_model=None,
)
def market_bars_v3(
    request: Request, payload: Any = Body(...)
) -> dict[str, Any] | JSONResponse:
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    try:
        request_data = _market_data_v3_request(payload, request_id)
        coverage_context = _market_data_v3_coverage_context(request_data)
        if coverage_context is None:
            _market_data_v3_error("insufficient_coverage", 422, request_data["requestId"])
        try:
            from src.services.thesis_ledger_provider_runtime import (
                DAILY_BAR_TARGET_TIMEOUT_SECONDS,
                get_thesis_ledger_runtime,
            )
            from api.thesis_ledger_multi_window_v3 import execute_market_window_v3, try_hithink_multi_window_v3
            runtime = get_thesis_ledger_runtime()
            multi_window = try_hithink_multi_window_v3(
                request_data, coverage_context, runtime,
                _market_data_v3_response, _market_data_v3_coverage_context,
                max_requests=8, timeout_seconds=DAILY_BAR_TARGET_TIMEOUT_SECONDS,
            )
            if multi_window is not None:
                return multi_window
            execution = execute_market_window_v3(request_data, runtime)
        except HTTPException:
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", ""))
            if code == "unsupported_adjustment":
                _market_data_v3_error("unsupported_price_basis", 422, request_data["requestId"])
            if code in {"not_covered", "insufficient_coverage"}:
                _market_data_v3_error("insufficient_coverage", 422, request_data["requestId"])
            if code in {"invalid_response", "upstream_invalid_response"}:
                _market_data_v3_error("invalid_response", 502, request_data["requestId"])
            logger.warning(
                "ThesisLedger Data V3 failed requestId=%s code=%s",
                request_data["requestId"],
                code or "upstream_failure",
            )
            _market_data_v3_error("upstream_failure", 503, request_data["requestId"])
        return _market_data_v3_response(request_data, execution, coverage_context)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content=error.detail)


@router_v3.post("/market/indicators/calculate", dependencies=[Depends(require_contract_token)])
def calculate_indicators(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    from src.services.technical_indicator_series import (
        build_indicator_points,
        normalize_indicator_parameters,
    )

    if payload.get("contractVersion") != 3:
        _error("invalid_request", "指标计算请求必须使用 Contract V3", 422)
    points = payload.get("points")
    input_fingerprint = str(payload.get("inputFingerprint") or "")
    if not isinstance(points, list) or not points or not input_fingerprint:
        _error("invalid_request", "指标计算请求缺少 points 或 inputFingerprint", 422)
    identity = payload.get("identity")
    if not isinstance(identity, dict) or not {"symbol", "assetType", "timeframe", "adjustment"}.issubset(identity):
        _error("invalid_request", "指标计算请求缺少 identity", 422)
    if _bar_series_fingerprint(points, identity) != input_fingerprint:
        _error("invalid_request", "inputFingerprint 与规范化 points 不一致", 422)
    requests = payload.get("requests")
    if not isinstance(requests, list) or not requests:
        _error("invalid_request", "指标计算请求至少包含一个指标", 422)
    import pandas as pd

    frame = pd.DataFrame(
        [
            {
                "date": item["timestamp"],
                "open": item["open"],
                "high": item["high"],
                "low": item["low"],
                "close": item["close"],
                "volume": item["volume"],
                "amount": item["amount"],
            }
            for item in points
        ]
    )
    results = []
    for item in requests:
        if not isinstance(item, dict):
            _error("invalid_request", "指标请求必须是对象", 422)
        name = str(item.get("name") or "").upper()
        try:
            parameters = normalize_indicator_parameters(name, item.get("parameters"))
            _, calculated_points, _, _ = build_indicator_points(
                frame, name, parameters, _iso_timestamp
            )
        except (KeyError, ValueError) as exc:
            _error("invalid_request", str(exc), 422)
        results.append(
            {
                "name": name,
                "parameters": parameters,
                "inputFingerprint": input_fingerprint,
                "points": [
                    {"timestamp": point["timestamp"], "values": point["values"]}
                    for point in calculated_points
                ],
            }
        )
    return {
        "contractVersion": 3,
        "engineVersion": "dsa-indicator-v3",
        "inputFingerprint": input_fingerprint,
        "results": results,
    }


def _session_close_available_at(occurred_at: str, fetched_at: str | None) -> str | None:
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
    from src.services.thesis_ledger_holding_rows import holding_period

    for _, row in frame.iterrows():
        try:
            quarter_rows.append((holding_period(row["季度"]), row))
        except ValueError:
            _error("upstream_invalid_response", "基金持仓披露报告期无法唯一识别", 502)
    if not quarter_rows:
        _error("upstream_invalid_response", "基金持仓披露缺少可识别的报告期", 502)
    latest = max(key for key, _ in quarter_rows)
    aggregate: dict[str, dict[str, Any]] = {}
    for key, row in quarter_rows:
        if key != latest:
            continue
        holding_symbol = _canonical_symbol(str(row["股票代码"]))
        weight = _number(row["占净值比例"], "weight") / 100
        if holding_symbol in aggregate:
            _error("upstream_invalid_response", "基金持仓披露同报告期代码重复", 502)
        aggregate[holding_symbol] = {
            "symbol": holding_symbol,
            "name": str(row["股票名称"]).strip(),
            "weight": weight,
        }
    holdings = sorted(aggregate.values(), key=lambda row: (-float(row["weight"]), row["symbol"]))
    if not holdings or sum(float(row["weight"]) for row in holdings) > 1.000001:
        _error("upstream_invalid_response", "基金持仓披露权重非法", 502)
    fetched_at = _now_iso()
    evidence = hashlib.sha256(
        json.dumps(holdings, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "version": 3,
        "fundSymbol": canonical,
        "reportPeriod": f"{latest[0]}-Q{latest[1]}",
        "disclosureDate": None,
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
        "version": 3,
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


@router_v3.get("/market/fx-rates", dependencies=[Depends(require_contract_token)])
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


@router_v3.get("/market/fund-nav", dependencies=[Depends(require_contract_token)])
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


@router_v3.get("/market/fund-nav/history", dependencies=[Depends(require_contract_token)])
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


@router_v3.get("/market/fund-holdings", dependencies=[Depends(require_contract_token)])
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


@router_v3.get("/market/quote", dependencies=[Depends(require_contract_token)])
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


@router_v3.get("/backtest/calendar", dependencies=[Depends(require_contract_token)])
def backtest_calendar(
    start: str = Query(..., min_length=10, max_length=10),
    end: str = Query(..., min_length=10, max_length=10),
    dataAsOf: str = Query(..., min_length=20),
    market: str = Query(..., min_length=2, max_length=2),
) -> dict[str, Any]:
    if market != "CN":
        _error("unsupported_capability", "当前合同仅支持 CN 市场交易日历", 422)
    try:
        data_as_of = parse_data_as_of(dataAsOf)
        start_date, end_date = validate_calendar_range(start, end, data_as_of)
    except DependencyFactError as exc:
        _error(exc.code, str(exc), exc.status_code)
    if _fixture_mode():
        fact = fixture_calendar(start, end, data_as_of, _fixture_cn_calendar_sources())
    else:
        fact = calendar_fact(start_date, end_date, data_as_of)
    if fact is None:
        return dependency_response(
            status="unavailable",
            provider="exchange-calendars",
            provider_revision="exchange-calendars-unavailable",
            coverage={"start": start, "end": end, "complete": False},
            facts=[],
            reason="CN 交易日历 Provider 当前不可用或未确认覆盖",
        )
    return dependency_response(
        status="supported",
        provider=str(fact["provider"]),
        provider_revision=str(fact["providerRevision"]),
        coverage={"start": start, "end": end, "complete": True},
        facts=[fact],
    )


@router_v3.get("/backtest/instrument-facts", dependencies=[Depends(require_contract_token)])
def backtest_instrument_facts(
    symbol: str = Query(..., min_length=1),
    market: str = Query(..., min_length=2, max_length=2),
    instrumentType: str = Query(..., min_length=1),
    dataAsOf: str = Query(..., min_length=20),
    start: str = Query(..., min_length=10, max_length=10),
    end: str = Query(..., min_length=10, max_length=10),
    executionStart: str = Query(..., min_length=10, max_length=10),
    executionEnd: str = Query(..., min_length=10, max_length=10),
    barAdjustment: str | None = Query(None, pattern="^(none|qfq|hfq)$"),
    barProviderId: str | None = Query(None, min_length=1),
    barUpstreamSource: str | None = Query(None, min_length=1),
    barRouteIndex: int | None = Query(None, ge=0, le=1),
    identityOnly: bool = Query(False),
) -> dict[str, Any]:
    try:
        canonical = validate_cn_stock(symbol, market, instrumentType, _canonical_symbol)
        data_as_of = parse_data_as_of(dataAsOf)
    except DependencyFactError as exc:
        _error(exc.code, str(exc), exc.status_code)
    facts = []
    if _fixture_mode():
        facts = _fixture_cn_instrument_facts(canonical, instrumentType)
    route_fields = (barAdjustment, barProviderId, barUpstreamSource, barRouteIndex)
    if any(field is not None for field in route_fields) and any(field is None for field in route_fields):
        _error("invalid_request", "精确日线来源参数必须一并提供", 422)
    route_key = None
    route_target = None
    if all(field is not None for field in route_fields):
        route_key = {
            "kind": "bar", "market": market, "assetType": instrumentType,
            "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": barAdjustment,
        }
        route_target = {
            "providerId": barProviderId, "upstreamSource": barUpstreamSource,
            "routeIndex": barRouteIndex,
        }
    try:
        return instrument_facts_response(
            canonical,
            data_as_of,
            start,
            end,
            executionStart,
            executionEnd,
            facts,
            instrument_type=instrumentType,
            route_key=route_key,
            route_target=route_target,
            identity_only=identityOnly,
        )
    except DependencyFactError as exc:
        _error(exc.code, str(exc), exc.status_code)


@router_v3.get("/market/chip", dependencies=[Depends(require_contract_token)])
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
    if value.get("contractVersion") != CONTROL_CONTRACT_V3_VERSION:
        error = ControlContractError(
            "CONTROL_CONTRACT_UNSUPPORTED",
            "Control 路由只支持 Contract V3",
            request_id=str(value.get("requestId") or ""),
            details={"supportedVersions": [CONTROL_CONTRACT_V3_VERSION]},
            contract_version=CONTROL_CONTRACT_V3_VERSION,
        )
        _control_http_error(error)
    if value.get("consumer") != CONSUMER_NAMESPACE:
        error = ControlContractError(
            "INVALID_CONSUMER",
            "Control Contract consumer namespace 不正确",
            request_id=str(value.get("requestId") or ""),
            contract_version=CONTROL_CONTRACT_V3_VERSION,
        )
        _control_http_error(error)
    return value


def _control_v3_envelope(payload: Any) -> dict[str, Any]:
    value = _control_envelope(payload)
    if "credential" in value:
        _control_http_error(
            ControlContractError(
                "INVALID_PROVIDER_CREDENTIALS",
                "Provider 凭证必须使用 credentials 字段",
                request_id=str(value.get("requestId") or ""),
                contract_version=CONTROL_CONTRACT_V3_VERSION,
            )
        )
    return value


@router_v3.post("/control/handshake", dependencies=[Depends(require_control_token)])
def control_handshake(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """协商独立 Control Contract，不返回任何凭证内容。"""
    value = _control_envelope(payload)
    request_id = value.get("requestId")
    if set(value) != {"contractVersion", "consumer", "requestId", "supportedVersions"}:
        _control_http_error(ControlContractError(
            "INVALID_HANDSHAKE", "Control Contract V3 handshake 字段不符合严格契约",
            request_id=str(request_id or ""), contract_version=CONTROL_CONTRACT_V3_VERSION,
        ))
    if not isinstance(request_id, str) or not request_id.strip():
        _control_http_error(ControlContractError(
            "INVALID_HANDSHAKE", "Control Contract V3 handshake requestId 不能为空",
            contract_version=CONTROL_CONTRACT_V3_VERSION,
        ))
    if value.get("supportedVersions") != [CONTROL_CONTRACT_V3_VERSION]:
        _control_http_error(ControlContractError(
            "CONTROL_CONTRACT_UNSUPPORTED", "没有共同的 Control Contract 版本",
            request_id=request_id.strip(),
            details={"supportedVersions": [CONTROL_CONTRACT_V3_VERSION]},
            contract_version=CONTROL_CONTRACT_V3_VERSION,
        ))
    return {
        "contractVersion": CONTROL_CONTRACT_V3_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "accepted": True,
        "providerRegistry": True,
        "policyApply": True,
        "catalogSync": True,
        "requestId": request_id.strip(),
    }


@router_v3.get("/control/providers", dependencies=[Depends(require_control_token)])
def control_providers() -> dict[str, Any]:
    return {
        "contractVersion": CONTROL_CONTRACT_V3_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "providers": _control_store().provider_registry(),
    }


@router_v3.get("/control/routes/capabilities", dependencies=[Depends(require_control_token)])
def control_route_capabilities_v3(
    contract_version: Optional[int] = Query(default=None, alias="contractVersion"),
) -> dict[str, Any]:
    if contract_version != CONTROL_CONTRACT_V3_VERSION:
        _control_http_error(
            ControlContractError(
                "CONTROL_CONTRACT_UNSUPPORTED",
                "路由能力目录只支持 Control Contract V3",
                details={"supportedVersions": [CONTROL_CONTRACT_V3_VERSION]},
                contract_version=CONTROL_CONTRACT_V3_VERSION,
            )
        )
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

        return get_thesis_ledger_runtime().market_route_catalog_v3()
    except Exception as exc:
        logger.warning(
            "ThesisLedger Control V3 route catalog unavailable error_type=%s",
            type(exc).__name__,
        )
        _control_http_error(
            ControlContractError(
                "ROUTE_CATALOG_UNAVAILABLE",
                "Control V3 精确路由能力目录暂时不可用",
                status_code=503,
                contract_version=CONTROL_CONTRACT_V3_VERSION,
            )
        )


@router_v3.post("/control/providers/{provider_id}/config", dependencies=[Depends(require_control_token)])
def control_provider_config(
    provider_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    try:
        value = _control_v3_envelope(payload)
        return _control_store().save_provider_config(provider_id, value)
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.post("/control/providers/{provider_id}/test", dependencies=[Depends(require_control_token)])
def control_provider_test(
    provider_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """执行只读、有限的逐 Capability smoke，并保证临时凭证不落库。"""
    try:
        value = _control_v3_envelope(payload)
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
        store = _control_store()
        draft_snapshot = store.provider_credential_snapshot(provider["providerId"])
        if "credentials" in value:
            try:
                patch = parse_credential_patch(provider["providerId"], value["credentials"])
                existing = None
                if (
                    draft_snapshot.source == "control"
                    and draft_snapshot.method == patch.method
                    and draft_snapshot.values
                ):
                    existing = CredentialValue(
                        method=draft_snapshot.method,
                        values=dict(draft_snapshot.values),
                    )
                merged = merge_credential_patch(provider["providerId"], patch, existing)
            except ValueError as exc:
                raise ControlContractError(
                    "INVALID_PROVIDER_CREDENTIALS",
                    str(exc),
                    request_id=str(value.get("requestId") or ""),
                ) from exc
            draft_snapshot = ProviderCredentialSnapshot.create(
                provider["providerId"],
                "control",
                merged.method,
                merged.values,
                draft_snapshot.config_version,
                draft_snapshot.credential_version,
            )
        elif str(value.get("credential") or "").strip():
            raise ControlContractError(
                "INVALID_PROVIDER_CREDENTIALS",
                "test API 仅接受结构化 credentials 草稿",
                request_id=str(value.get("requestId") or ""),
            )
        credential_configured = bool(
            draft_snapshot.values or not provider["requiresCredential"]
        )
        configured = bool(provider["configured"] or credential_configured)
        if not configured:
            capability_results = {
                capability: {
                    "status": "unconfigured",
                    "readOnly": True,
                    "attempted": False,
                    "errorCode": "not_configured",
                }
                for capability in provider["capabilities"]
            }
            status = "unconfigured"
        else:
            capability_results: dict[str, dict[str, Any]] = {}
            for capability in provider["capabilities"]:
                try:
                    if _fixture_mode():
                        capability_results[capability] = {
                            "status": "unavailable",
                            "readOnly": True,
                            "attempted": False,
                            "errorCode": "fixture_mode",
                        }
                    else:
                        from src.services.thesis_ledger_provider_runtime import (
                            get_thesis_ledger_runtime,
                        )

                        capability_results[capability] = get_thesis_ledger_runtime().smoke(
                            provider["providerId"],
                            capability,
                            credential_snapshot=draft_snapshot,
                            draft_probe="credentials" in value,
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
            "contractVersion": CONTROL_CONTRACT_V3_VERSION,
            "consumer": CONSUMER_NAMESPACE,
            "providerId": provider["providerId"],
            "status": status,
            "credentialConfigured": credential_configured,
            "capabilityResults": capability_results,
            "requestId": str(value.get("requestId") or _request_id_context.get() or uuid.uuid4()),
        }
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.post("/control/providers/{provider_id}/remove", dependencies=[Depends(require_control_token)])
def control_provider_remove(
    provider_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    try:
        value = _control_v3_envelope(payload)
        return _control_store().remove_provider(provider_id, value)
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.post("/control/policies/apply", dependencies=[Depends(require_control_token)])
def control_apply_policy(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        if not isinstance(payload, dict) or payload.get("contractVersion") != CONTROL_CONTRACT_V3_VERSION:
            raise ControlContractError(
                "CONTROL_CONTRACT_UNSUPPORTED",
                "策略写入只支持 Control Contract V3",
                details={"supportedVersions": [CONTROL_CONTRACT_V3_VERSION]},
                contract_version=CONTROL_CONTRACT_V3_VERSION,
            )
        value = _control_envelope(payload)
        return _control_store().apply_policy_v3(value)
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.get("/control/policies/effective", dependencies=[Depends(require_control_token)])
def control_effective_policy(
    contract_version: Optional[int] = Query(default=None, alias="contractVersion"),
) -> dict[str, Any]:
    if contract_version not in (None, CONTROL_CONTRACT_V3_VERSION):
        _control_http_error(ControlContractError(
            "CONTROL_CONTRACT_UNSUPPORTED",
            "生效策略只支持 Control Contract V3",
            details={"supportedVersions": [CONTROL_CONTRACT_V3_VERSION]},
            contract_version=CONTROL_CONTRACT_V3_VERSION,
        ))
    store = _control_store()
    projection = store.policy_projection_v3()
    return {
        "contractVersion": CONTROL_CONTRACT_V3_VERSION,
        "consumer": CONSUMER_NAMESPACE,
        "projection": {"effective": projection["effective"]} if projection else None,
    }


@router_v3.get("/catalog/snapshot", dependencies=[Depends(require_contract_token)])
def catalog_snapshot(cursor: Optional[str] = Query(default=None)) -> dict[str, Any]:
    try:
        return _control_store().catalog_snapshot(cursor)
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.get("/catalog/delta", dependencies=[Depends(require_contract_token)])
def catalog_delta(cursor: str = Query(..., min_length=1)) -> dict[str, Any]:
    try:
        return _control_store().catalog_delta(cursor)
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.post("/control/catalog/jobs", dependencies=[Depends(require_control_token)])
def control_catalog_job(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        _control_v3_envelope(payload)
        return _control_store().trigger_catalog_job(payload=payload)
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.get("/control/catalog/jobs/{job_id}", dependencies=[Depends(require_control_token)])
def control_catalog_job_status(job_id: str) -> dict[str, Any]:
    try:
        return _control_store().get_catalog_job(job_id)
    except ControlContractError as error:
        _control_http_error(error)


@router_v3.post("/control/catalog/ack", dependencies=[Depends(require_control_token)])
def control_catalog_ack(payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    try:
        _control_v3_envelope(payload)
        return _control_store().catalog_ack(payload)
    except ControlContractError as error:
        _control_http_error(error)
