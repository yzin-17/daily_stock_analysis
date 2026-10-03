"""Tushare 基金历史端点共用的标的、日期和请求分段预算。"""

from datetime import date, timedelta
import re


def require_cn_etf_symbol(symbol):
    if not isinstance(symbol, str) or not re.fullmatch(
        r"(?:(?:51|52|56|58)\d{4}\.SH|(?:15|16|18)\d{4}\.SZ)", symbol,
    ):
        raise ValueError("Tushare exact history requires a CN ETF symbol")


def parse_tushare_date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-?\d{2}-?\d{2}", value):
        raise ValueError("invalid Tushare history date")
    compact = value.replace("-", "")
    result = date(int(compact[:4]), int(compact[4:6]), int(compact[6:8]))
    if value not in (result.isoformat(), result.strftime("%Y%m%d")):
        raise ValueError("invalid Tushare history date")
    return result


def fund_history_windows(symbol, start, end, max_calls=32):
    if not isinstance(symbol, str) or not re.fullmatch(r"\d{6}\.(SH|SZ)", symbol):
        raise ValueError("invalid Tushare fund symbol")
    first, last = parse_tushare_date(start), parse_tushare_date(end)
    if first > last or type(max_calls) is not int or not 1 <= max_calls <= 64:
        raise ValueError("invalid Tushare fund range or budget")
    count = (last - first).days // 366 + 1
    if count > max_calls:
        raise ValueError("Tushare fund request budget exceeded")
    result = []
    for index in range(count):
        begin = first + timedelta(days=index * 366)
        stop = begin + timedelta(days=min(365, (last - begin).days))
        result.append((begin, stop))
    return tuple(result)
