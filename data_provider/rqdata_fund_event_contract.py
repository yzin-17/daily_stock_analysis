"""RQData 基金事件共用的日期和显式查询身份契约。"""

from datetime import date, datetime
import re


def effective_date(value):
    if isinstance(value, datetime):
        if value.tzinfo is not None or value.time() != datetime.min.time():
            raise ValueError("fund event requires a date without intraday time")
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        parsed = date.fromisoformat(value)
        if parsed.isoformat() == value:
            return value
    raise ValueError("fund event invalid effective date")


def validate_fund_event_scope(symbol, query_fund_code, instrument_type, start, end):
    if not isinstance(query_fund_code, str) or not re.fullmatch(r"\d{6}", query_fund_code):
        raise ValueError("fund event requires exact query identity")
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(SH|SZ|OF)", symbol):
        raise ValueError("fund event invalid canonical symbol")
    if instrument_type not in {"ETF", "NAV_FUND"} or (instrument_type == "ETF" and symbol.endswith(".OF")):
        raise ValueError("fund event invalid asset identity")
    if effective_date(start) != start or effective_date(end) != end or start > end:
        raise ValueError("fund event invalid date window")
