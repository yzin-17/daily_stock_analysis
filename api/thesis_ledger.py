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
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal, Mapping, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
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
    parse_data_as_of,
    response as dependency_response,
    instrument_facts_response,
)
from src.services.thesis_ledger_reference_identity import validate_instrument_request
from src.services.thesis_ledger_market_v3_facts import (
    canonical_market_coverage_proof_v3,
    market_calendar_evidence_v3,
)
from src.services.thesis_ledger_research_calendar import research_market_coverage_context

from api.thesis_ledger_auth import (
    CONTRACT_VERSION as CONTRACT_VERSION, _error, _request_id_context, require_contract_token, require_control_token,
)
PROVIDER_ID = "akshare"
ENGINE_VERSION = "dsa-thesis-ledger-v1"
LOCAL_FIXTURE_VERSION = "dsa-thesis-ledger-fixture-v1"
MARKET_DATA_V3_METHOD_VERSION = "dsa-market-bars-v3-native-source-v1"
from src.services.thesis_ledger_market_v3_adapters import market_v3_bar_provider_ids, MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS
FX_CURRENCIES = {"CNY", "HKD", "USD"}
FX_MAX_AGE_DAYS = 7
logger = logging.getLogger(__name__)

router_v3 = APIRouter(
    prefix="/thesis-ledger",
    tags=["ThesisLedger Market Contract V3"],
)

from api.thesis_ledger_reference_data import build_reference_data_router
router_v3.include_router(build_reference_data_router())

from src.services.thesis_ledger_market_errors_v3 import market_runtime_error
from api.thesis_ledger_market_errors import market_data_error_v3 as _market_data_v3_error


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
    from pydantic import ValidationError
    from src.services.thesis_ledger_fund_nav_contract_v1 import LatestNavResult
    canonical = _canonical_fund_symbol(symbol)
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway
        gateway_result = get_thesis_ledger_data_gateway().fund_nav(canonical, request_id=request_id)
    except Exception as exc:  # noqa: BLE001 - gateway maps provider failures to diagnostics.
        _data_gateway_error(exc, "基金单位净值暂时不可用", no_eligible_message="当前策略没有可用的基金净值 Provider")
    try:
        nav = LatestNavResult.model_validate(gateway_result.data)
    except ValidationError:
        _error("upstream_invalid_response", "最新净值未通过标准结果合同", 502)
    if nav.symbol != canonical or nav.provider != gateway_result.provider:
        _error("upstream_invalid_response", "最新净值证券或来源不匹配", 502)
    parsed_date = datetime.fromisoformat(nav.point.nav_date)
    age_days = max(0, (datetime.now(timezone.utc).date() - parsed_date.date()).days)
    return {
        "version": 3,
        "symbol": canonical,
        "unitNav": nav.point.unit_nav,
        "navDate": nav.point.nav_date,
        "provider": gateway_result.provider,
        "fetchedAt": _now_iso(),
        "freshness": "stale" if age_days > 7 else "delayed",
        "fallbackUsed": gateway_result.fallback_used,
    }


def _real_fund_nav_history(
    symbol: str,
    start: Optional[str],
    end: Optional[str],
    limit: int,
    request_id: str | None = None,
) -> list[dict[str, Any]]:
    from pydantic import ValidationError
    from src.services.thesis_ledger_fund_nav_history_contract_v1 import NavHistoryResult
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
        provider = gateway_result.provider
        fallback_used = gateway_result.fallback_used
    except Exception as exc:  # noqa: BLE001 - gateway exposes stable error codes.
        _data_gateway_error(
            exc,
            "基金净值历史暂时不可用",
            no_eligible_message="当前策略没有可用的基金净值历史 Provider",
        )

    try:
        history = NavHistoryResult.model_validate(gateway_result.data)
    except ValidationError:
        _error("upstream_invalid_response", "净值历史未通过标准结果合同", 502)
    if history.symbol != canonical or history.provider != provider:
        _error("upstream_invalid_response", "净值历史证券或来源不匹配", 502)
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
    for point in history.points:
        nav_date = point.nav_date
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
                "unitNav": point.unit_nav,
                "navDate": nav_date,
                "provider": provider,
                "fetchedAt": fetched_at,
                "freshness": "stale" if age_days > 7 else "delayed",
                "fallbackUsed": fallback_used,
            }
        )
    return result[-limit:]


def _real_quote(symbol: str, request_id: str | None = None, asset_type: str | None = None) -> dict[str, Any]:
    request_id = request_id or _request_id_context.get()
    try:
        from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_data_gateway

        gateway_result = get_thesis_ledger_data_gateway().quote(
            symbol,
            request_id=request_id, **({"instrument_type": asset_type} if asset_type is not None else {}),
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

    from src.services.thesis_ledger_quote_result_contract import QuoteResult
    upstream_source = quote.upstream_source if isinstance(quote, QuoteResult) else _upstream_source(getattr(quote, "source", None), provider)
    from api.thesis_ledger_quote_fields import quote_response_payload

    result = quote_response_payload(quote, symbol=_canonical_symbol(symbol), provider=provider,
        fallback_used=fallback_used, source=upstream_source,
        fetched_at=_iso_timestamp(getattr(quote, "fetched_at", None) or _now_iso()), number=_number, timestamp=_iso_timestamp)
    from api.thesis_ledger_quote_units import apply_quote_public_units
    return apply_quote_public_units(result, provider, upstream_source, quote, _error)


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

    if route_key["market"] == "CN" and instrument_type_for_symbol(symbol) != route_key["assetType"]:
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
        if value["tradabilityMode"] != "assume-untradable-no-bar" or route_key["market"] not in {"CN", "HK", "US"} or route_key["timeframe"] != "1d":
            _market_data_v3_error("invalid_response", 422, request_id)
        request_data["tradabilityMode"] = value["tradabilityMode"]
    return request_data


def _market_data_v3_coverage_context(request_data: Mapping[str, Any]) -> dict[str, Any] | None:
    """Provider 请求前解析上市与日历，研究窗口可复用核验后的现行日历。"""
    return research_market_coverage_context(request_data, calendar_evidence_provider=market_calendar_evidence_v3)


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
        provider not in market_v3_bar_provider_ids()
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
    from src.services.thesis_ledger_zero_volume_observations import executable_research_frame
    frame = executable_research_frame(request_data, getattr(execution, "value", None), _market_data_v3_error)
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
        from api.thesis_ledger_market_daily_points import MarketDailyPointSource
        point_source = MarketDailyPointSource(request_data, fetched_at, _iso_timestamp, _bar_series_point)
        for _, row in frame.iterrows():
            timestamp = point_source.timestamp(row.get("date"))
            trading_date = datetime.fromisoformat(timestamp).astimezone(
                ZoneInfo(point_source.timezone)
            ).date().isoformat()
            if not request_data["start"] <= trading_date <= request_data["end"]:
                _market_data_v3_error("invalid_response", 502, request_id)
            from api.thesis_ledger_exact_bar_fields import bar_fields
            fields = bar_fields(row, _number)
            if any(number < 0 for number in fields.values() if number is not None):
                _market_data_v3_error("invalid_response", 502, request_id)
            if fields["high"] < max(fields["open"], fields["close"], fields["low"]):
                _market_data_v3_error("invalid_response", 502, request_id)
            if fields["low"] > min(fields["open"], fields["close"], fields["high"]):
                _market_data_v3_error("invalid_response", 502, request_id)
            item = point_source.point({"timestamp": timestamp, **fields})
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
            from api.thesis_ledger_multi_window_v3 import execute_market_window_v3, try_daily_multi_window_v3
            runtime = get_thesis_ledger_runtime()
            multi_window = try_daily_multi_window_v3(
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
            logger.warning(
                "ThesisLedger Data V3 failed requestId=%s code=%s",
                request_data["requestId"],
                code or "upstream_failure",
            )
            mapped, status = market_runtime_error(exc)
            _market_data_v3_error(mapped, status, request_data["requestId"])
        return _market_data_v3_response(request_data, execution, coverage_context)
    except HTTPException as error:
        return JSONResponse(status_code=error.status_code, content=error.detail)


@router_v3.get("/market/indicators/capabilities", dependencies=[Depends(require_contract_token)])
def period_indicator_capabilities_v3() -> dict[str, Any]:
    from src.services.thesis_ledger_period_indicator_contract import period_indicator_capabilities
    return period_indicator_capabilities()


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
    from src.services.thesis_ledger_period_indicator_contract import validate_indicator_fingerprint
    try:
        validate_indicator_fingerprint(payload, lambda: _bar_series_fingerprint(points, identity))
    except (ValueError, KeyError, TypeError, OverflowError) as exc:
        _error("invalid_request", str(exc), 422)
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


def _fund_holdings_payload(symbol: str, value: Any, provider: str, fallback_used: bool) -> dict[str, Any]:
    from pydantic import ValidationError
    from src.services.thesis_ledger_fund_holdings_contract_v1 import HoldingsResult
    canonical = _canonical_fund_symbol(symbol)
    try:
        holdings = HoldingsResult.model_validate(value)
    except ValidationError:
        _error("upstream_invalid_response", "基金持仓未通过标准结果合同", 502)
    if holdings.symbol != canonical or holdings.provider != provider:
        _error("upstream_invalid_response", "基金持仓证券或来源不匹配", 502)
    return {
        "version": 3,
        "fundSymbol": canonical,
        "reportPeriod": holdings.report_period,
        "disclosureDate": holdings.disclosure_date,
        "provider": provider,
        "fetchedAt": _now_iso(),
        "evidenceVersion": holdings.evidence_version,
        "fallbackUsed": fallback_used,
        "holdings": [holding.model_dump() for holding in holdings.holdings],
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
    from pydantic import ValidationError
    from src.services.thesis_ledger_chip_summary_contract_v1 import ChipResult
    try:
        chip = ChipResult.model_validate(chip)
    except ValidationError:
        _error("upstream_invalid_response", "筹码摘要未通过标准合同", 502)
    if chip.symbol != _canonical_symbol(symbol) or chip.provider != provider:
        _error("upstream_invalid_response", "筹码摘要证券或来源不匹配", 502)
    return {
        "version": 3,
        "symbol": chip.symbol,
        "averageCost": chip.average_cost,
        "profitRatio": chip.profit_ratio,
        "range70": list(chip.range70),
        "range90": list(chip.range90),
        "concentration": chip.concentration90,
        "provider": provider,
        "fallbackUsed": fallback_used,
        "engineVersion": ENGINE_VERSION,
        "calculatedAt": _iso_timestamp(chip.source_reported_date or chip.observed_at),
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
    assetType: Literal["STOCK", "ETF", "INDEX"] | None = Query(None),
) -> dict[str, Any]:
    request_id = request.headers.get("x-request-id") or _request_id_context.get()
    return (
        _fixture_quote(symbol)
        if _fixture_mode()
        else _real_quote(symbol, request_id=request_id, asset_type=assetType)
    )


@router_v3.get("/backtest/calendar", dependencies=[Depends(require_contract_token)])
def backtest_calendar(
    start: str = Query(..., min_length=10, max_length=10),
    end: str = Query(..., min_length=10, max_length=10),
    dataAsOf: str = Query(..., min_length=20),
    market: str = Query(..., min_length=2, max_length=2),
) -> dict[str, Any]:
    from src.services.thesis_ledger_calendar_contract import calendar_request_response
    try:
        return calendar_request_response(start, end, dataAsOf, market,
            _fixture_cn_calendar_sources() if _fixture_mode() else None)
    except DependencyFactError as exc:
        _error(exc.code, str(exc), exc.status_code)


@router_v3.get("/market/calendar", dependencies=[Depends(require_contract_token)])
def market_calendar(start: str, end: str, dataAsOf: str, market: str) -> dict[str, Any]:
    from src.services.thesis_ledger_calendar_contract import calendar_request_response
    try:
        return calendar_request_response(start, end, dataAsOf, market,
            _fixture_cn_calendar_sources() if _fixture_mode() else None, display=True)
    except DependencyFactError as exc:
        _error(exc.code, str(exc), exc.status_code)


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
        canonical = validate_instrument_request(symbol, market, instrumentType, _canonical_symbol, identityOnly, barAdjustment, barProviderId, barUpstreamSource)
        data_as_of = parse_data_as_of(dataAsOf)
    except DependencyFactError as exc:
        _error(exc.code, str(exc), exc.status_code)
    facts = []
    if _fixture_mode() and market == "CN":
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
            identity_only=identityOnly, market=market,
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
    contract_version: Optional[int] = Query(default=None, alias="contractVersion"), event_admissions: Optional[Literal['v1']] = Query(default=None, alias="eventAdmissions"),
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

        return get_thesis_ledger_runtime().market_route_catalog_v3(**({'event_admissions': True} if event_admissions == 'v1' else {}))
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
    """逐精确能力检查，草稿与诊断结果不写入业务配置。"""
    from src.services.thesis_ledger_provider_exact_checks import check_provider
    from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime

    try:
        value = _control_v3_envelope(payload)
        return check_provider(get_thesis_ledger_runtime(), provider_id, value, fixture=_fixture_mode())
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
