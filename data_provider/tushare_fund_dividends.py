"""Tushare 基金分红观测标准化，不从返回记录推断完整历史。"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re

import pandas as pd

from .tushare_history_window import parse_tushare_date

DATE_FIELDS = {
    "ann_date": "announcementDate", "imp_anndate": "implementationAnnouncementDate",
    "base_date": "incomeBaseDate", "record_date": "recordDate", "ex_date": "effectiveDate",
    "pay_date": "paymentDate", "earpay_date": "incomePaymentDate",
    "net_ex_date": "navExDate", "account_date": "reinvestmentAccountDate",
}


def _optional_date(value):
    if value is None or pd.isna(value) or (isinstance(value, str) and value.strip() in {"", "--", "-"}):
        return None
    return parse_tushare_date(value).isoformat()


def _cash(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0:
            raise ValueError()
    except (ValueError, InvalidOperation):
        raise ValueError("fund_div invalid cash amount") from None
    cash = format(amount, "f")
    return cash.rstrip("0").rstrip(".") if "." in cash else cash


def validate_fund_dividend_scope(symbol, instrument_type, currency, start, end):
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|OF)", symbol):
        raise ValueError("fund_div requires a canonical fund symbol")
    if instrument_type not in {"ETF", "NAV_FUND"} or (instrument_type == "ETF" and symbol.endswith(".OF")):
        raise ValueError("fund_div inconsistent instrument identity")
    if currency not in {"CNY", "HKD", "USD"}:
        raise ValueError("fund_div requires verified instrument currency")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first.isoformat() != start or last.isoformat() != end or first > last:
        raise ValueError("fund_div invalid effective window")


def normalize_tushare_fund_dividends(
    frame, symbol, *, instrument_type, currency, start, end, observed_at, provider_revision,
):
    """调用方提供已核验标的类型和分红币种；每份金额不除以 base_unit。"""
    validate_fund_dividend_scope(symbol, instrument_type, currency, start, end)
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("fund_div observed time requires timezone")
    if not isinstance(provider_revision, str) or not provider_revision.strip():
        raise ValueError("fund_div source revision missing")
    required = {"ts_code", "ann_date", "imp_anndate", "div_proc", "record_date", "ex_date", "pay_date", "div_cash"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns) or frame.columns.duplicated().any():
        raise ValueError("fund_div response fields missing or duplicate")
    fetched = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    observations, facts = [], {}
    for row in frame.to_dict("records"):
        if row["ts_code"] != symbol:
            raise ValueError("fund_div source symbol mismatch")
        dates = {name: _optional_date(row.get(field)) for field, name in DATE_FIELDS.items()}
        progress = row["div_proc"]
        if not isinstance(progress, str) or not progress.strip():
            raise ValueError("fund_div plan progress missing")
        progress = progress.strip()
        effective = dates["effectiveDate"]
        if progress == "实施" and effective is None:
            raise ValueError("implemented fund_div requires ex_date")
        if effective is not None and not start <= effective <= end:
            continue
        cash = None
        if progress == "实施" or (row["div_cash"] is not None and not pd.isna(row["div_cash"])):
            cash = _cash(row["div_cash"])
        if progress == "实施":
            if (dates["recordDate"] and dates["recordDate"] > effective) or (
                dates["paymentDate"] and dates["paymentDate"] < effective
            ):
                raise ValueError("fund_div conflicting economic dates")
        observation = {
            **dates, "planProgress": progress, "cashAmount": cash, "currency": currency,
            "cashUnit": "per-fund-unit", "observedAt": fetched, "endpoint": "fund_div", "upstreamSource": "tushare",
        }
        if observation not in observations:
            observations.append(observation)
        if progress != "实施":
            continue
        fact = {
            "symbol": symbol, "market": "CN", "instrumentType": instrument_type, "type": "CASH_DIVIDEND",
            "effectiveDate": effective, "cashAmount": cash, "currency": currency,
            "occurredAt": f"{effective}T00:00:00+08:00", "availableAt": fetched,
            "provider": "tushare", "providerRevision": provider_revision,
        }
        for field in ("recordDate", "paymentDate"):
            if dates[field] is not None:
                fact[field] = dates[field]
        implementation = dates["implementationAnnouncementDate"]
        if implementation is not None:
            visible = max(value for value in (implementation, dates["announcementDate"]) if value is not None)
            fact["strategyVisibility"] = {"kind": "conservative-day", "visibleDate": visible}
        if effective in facts and facts[effective] != fact:
            raise ValueError("fund_div conflicting implemented records")
        facts[effective] = fact
    if any(item["effectiveDate"] in facts and item["planProgress"] != "实施" for item in observations):
        raise ValueError("fund_div conflicting plan progress requires revision evidence")
    observations.sort(key=lambda item: (item["effectiveDate"] or "", item["implementationAnnouncementDate"] or "", item["planProgress"]))
    return {
        "facts": [facts[key] for key in sorted(facts)], "observations": observations,
        "coverage": {"start": start, "end": end, "complete": False},
    }
