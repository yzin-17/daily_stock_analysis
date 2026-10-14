"""中国内地原生日线请求；显式传递资产、来源、复权及日期。"""

from .exact_daily_contract import ExactDailyError


def read_cn_exact_daily(provider_id, adapter, request):
    symbol = request.symbol
    code, exchange = symbol.split(".", 1)
    if exchange not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
        raise ExactDailyError("invalid_request", "中国内地日线需要完整证券代码")
    if provider_id == "efinance":
        from .thesis_ledger_sources.efinance import read_frame
        return read_frame(request)
    if provider_id == "baostock":
        from .thesis_ledger_sources.baostock import read_daily_frame
        return read_daily_frame(adapter, request)
    if provider_id == "tickflow":
        from .thesis_ledger_sources.tickflow import read_frame
        return read_frame(adapter, request)
    if provider_id == "pytdx":
        from .thesis_ledger_sources.pytdx import read_frame
        return read_frame(adapter, request)
    if provider_id == "tushare":
        from .thesis_ledger_sources.tushare import read_frame
        return read_frame(adapter, request, "CN")
    raise ExactDailyError("not_adapted", "来源尚未接入精确中国内地日线")
