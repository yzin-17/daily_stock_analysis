"""RQData 基金每份税前分红事实，不推断公告可见性或历史完整性。"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd

from .rqdata_fund_event_contract import effective_date, validate_fund_event_scope


def normalize_rqdata_fund_dividends(
    frame, symbol, *, query_fund_code, instrument_type, currency, start, end, observed_at, provider_revision,
):
    validate_fund_event_scope(symbol, query_fund_code, instrument_type, start, end)
    if currency not in {"CNY", "HKD", "USD"}:
        raise ValueError("fund dividend requires verified currency")
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("fund dividend requires observed timezone")
    if not isinstance(provider_revision, str) or not provider_revision.strip():
        raise ValueError("fund dividend requires revision")
    required = {"book_closure_date", "payable_date", "dividend_before_tax"}
    if not isinstance(frame, pd.DataFrame) or not required.issubset(frame.columns) or frame.columns.duplicated().any():
        raise ValueError("fund dividend invalid response fields")
    if isinstance(frame.index, pd.MultiIndex):
        raise ValueError("fund dividend requires single fund response")
    if "ex_dividend_date" not in frame:
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.name not in {None, "ex_dividend_date"}:
            raise ValueError("fund dividend missing effective dates")
        frame = frame.assign(ex_dividend_date=frame.index)
    elif isinstance(frame.index, pd.DatetimeIndex):
        if any(effective_date(index) != effective_date(value)
               for index, value in zip(frame.index, frame["ex_dividend_date"])):
            raise ValueError("fund dividend conflicting date index and column")
    fetched = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    facts = {}
    for row in frame.to_dict("records"):
        if "order_book_id" in row and row["order_book_id"] != query_fund_code:
            raise ValueError("fund dividend identity mismatch")
        effective = effective_date(row["ex_dividend_date"])
        dates = {}
        for field, name in (("book_closure_date", "recordDate"), ("payable_date", "paymentDate")):
            value = row[field]
            if value is not None and not pd.isna(value):
                dates[name] = effective_date(value)
        if dates.get("recordDate", effective) > effective or dates.get("paymentDate", effective) < effective:
            raise ValueError("fund dividend conflicting economic dates")
        try:
            cash = Decimal(str(row["dividend_before_tax"]))
            if not cash.is_finite() or cash <= 0:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise ValueError("fund dividend invalid cash") from None
        if not start <= effective <= end:
            continue
        cash_text = format(cash, "f")
        if "." in cash_text:
            cash_text = cash_text.rstrip("0").rstrip(".")
        fact = {
            "symbol": symbol, "market": "CN", "instrumentType": instrument_type, "type": "CASH_DIVIDEND",
            "effectiveDate": effective, **dates, "cashAmount": cash_text, "currency": currency,
            "occurredAt": f"{effective}T00:00:00+08:00", "availableAt": fetched,
            "provider": "rqdata", "providerRevision": provider_revision,
        }
        if effective in facts and facts[effective] != fact:
            raise ValueError("fund dividend conflicting records")
        facts[effective] = fact
    return {
        "facts": [facts[key] for key in sorted(facts)], "providerRevision": provider_revision,
        "coverage": {"start": start, "end": end, "complete": False},
    }
