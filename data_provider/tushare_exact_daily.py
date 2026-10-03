"""Tushare ETF 原始日线的精确来源入口，不隐式切换端点或重试。"""

from .tushare_history_request import TushareHistoryRequest
from .tushare_history_window import require_cn_etf_symbol
from .tushare_fund_daily import PAGINATION_PROTOCOL, fetch_tushare_fund_daily


class TushareExactDailyMixin:
    def get_daily_data_for_source(
        self, stock_code, upstream_source, *, start_date=None, end_date=None,
        days=30, adjustment="none", timeout_seconds=15,
    ):
        if upstream_source != "tushare" or adjustment != "none":
            raise ValueError("unsupported Tushare source or adjustment")
        symbol = self._convert_stock_code(stock_code)
        require_cn_etf_symbol(symbol)
        if not start_date or not end_date:
            raise ValueError("Tushare exact daily requires an explicit date window")
        request = TushareHistoryRequest(self, timeout_seconds)
        raw = fetch_tushare_fund_daily(
            request.client, symbol, start_date, end_date, before_call=request.before_call,
        )
        request.check_complete()
        result = self._normalize_data(raw, symbol)
        result.attrs["upstream_source"] = "tushare"
        partitions = raw.attrs["fundDailyRetrieval"]["partitions"]
        result.attrs["thesis_ledger_v3_pagination"] = {
            "status": "complete", "pagesFetched": len(partitions), "continuationPending": False,
            "protocol": PAGINATION_PROTOCOL, "maximumRows": None,
            "requestedStart": partitions[0]["start"], "requestedEnd": partitions[-1]["end"],
        }
        return result
