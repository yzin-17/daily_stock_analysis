"""RQData 基金拆分标准化；代码映射由已核验的查询上下文提供。"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import pandas as pd
from .rqdata_fund_event_contract import effective_date as _effective_date, validate_fund_event_scope


def normalize_rqdata_fund_splits(
    frame, symbol, *, query_fund_code, instrument_type, start, end, observed_at, provider_revision,
):
    """单基金响应只可使用调用方核实的 query_fund_code 与 symbol 绑定。"""
    validate_fund_event_scope(symbol, query_fund_code, instrument_type, start, end)
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("fund split requires observed timezone")
    if not isinstance(provider_revision, str) or not provider_revision.strip():
        raise ValueError("fund split requires source revision")
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any() or "split_ratio" not in frame:
        raise ValueError("fund split invalid response fields")
    if isinstance(frame.index, pd.MultiIndex):
        raise ValueError("fund split requires single fund response")
    if "ex_dividend_date" in frame and isinstance(frame.index, pd.DatetimeIndex):
        if any(_effective_date(index) != _effective_date(value)
               for index, value in zip(frame.index, frame["ex_dividend_date"])):
            raise ValueError("fund split conflicting date index and column")
    if "ex_dividend_date" not in frame:
        if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.name not in {None, "ex_dividend_date"}:
            raise ValueError("fund split missing effective dates")
        frame = frame.assign(ex_dividend_date=frame.index)
    fetched = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    observations, facts = {}, {}
    for row in frame.to_dict("records"):
        if "order_book_id" in row and row["order_book_id"] != query_fund_code:
            raise ValueError("fund split response identity mismatch")
        effective = _effective_date(row["ex_dividend_date"])
        try:
            ratio = Decimal(str(row["split_ratio"]))
            if not ratio.is_finite() or ratio <= 0:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise ValueError("fund split invalid ratio") from None
        if not start <= effective <= end:
            continue
        ratio_text = format(ratio, "f")
        if "." in ratio_text:
            ratio_text = ratio_text.rstrip("0").rstrip(".")
        observation = {"effectiveDate": effective, "ratio": ratio_text, "queryFundCode": query_fund_code,
                       "observedAt": fetched, "endpoint": "fund.get_split"}
        if effective in observations and observations[effective] != observation:
            raise ValueError("fund split conflicting records")
        observations[effective] = observation
        if ratio == 1:
            continue
        facts[effective] = {
            "symbol": symbol, "market": "CN", "instrumentType": instrument_type,
            "type": "SPLIT" if ratio > 1 else "REVERSE_SPLIT", "ratio": ratio_text,
            "effectiveDate": effective, "occurredAt": f"{effective}T00:00:00+08:00",
            "availableAt": fetched, "provider": "rqdata", "providerRevision": provider_revision,
        }
    return {
        "facts": [facts[key] for key in sorted(facts)],
        "observations": [observations[key] for key in sorted(observations)],
        "providerRevision": provider_revision, "coverage": {"start": start, "end": end, "complete": False},
    }
