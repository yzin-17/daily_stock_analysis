"""基金复权因子原始观测；不授予锚点、历史可见时间或价格转换资格。"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

import pandas as pd

from .tushare_history_request import TushareHistoryRequest
from .tushare_history_window import fund_history_windows, parse_tushare_date, require_cn_etf_symbol

FIELDS = "ts_code,trade_date,adj_factor"
PROTOCOL = "tushare-fund-adj-date-partitions-366-v1"


def _rows(frame, symbol, start, end):
    if (
        not isinstance(frame, pd.DataFrame) or not set(FIELDS.split(",")).issubset(frame.columns)
        or frame.columns.duplicated().any() or len(frame) > (end - start).days + 1
    ):
        raise ValueError("fund_adj invalid response shape")
    result, seen = [], set()
    for row in frame.to_dict("records"):
        day = parse_tushare_date(row["trade_date"])
        if row["ts_code"] != symbol or not start <= day <= end or day in seen:
            raise ValueError("fund_adj symbol/date mismatch or duplicate")
        seen.add(day)
        try:
            value = row["adj_factor"]
            if isinstance(value, bool):
                raise ValueError()
            number = Decimal(str(value))
            if not number.is_finite() or number <= 0:
                raise ValueError()
        except (ValueError, InvalidOperation):
            raise ValueError("fund_adj invalid factor") from None
        result.append({"ts_code": symbol, "trade_date": day.strftime("%Y%m%d"), "adj_factor": str(number)})
    return sorted(result, key=lambda row: row["trade_date"])


def fetch_tushare_fund_adj(api, symbol, start, end, *, before_call, max_calls=32):
    require_cn_etf_symbol(symbol)
    windows = fund_history_windows(symbol, start, end, max_calls)
    rows, partitions = [], []
    for begin, stop in windows:
        before_call()
        values = _rows(api.fund_adj(
            ts_code=symbol, start_date=begin.strftime("%Y%m%d"), end_date=stop.strftime("%Y%m%d"),
            fields=FIELDS, limit=2000,
        ), symbol, begin, stop)
        digest = sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        partitions.append({"start": begin.isoformat(), "end": stop.isoformat(), "rows": len(values), "sha256": digest})
        rows.extend(values)
    frame = pd.DataFrame(rows, columns=FIELDS.split(","))
    frame.attrs["upstream_source"] = "tushare"
    frame.attrs["fundAdjustmentRetrieval"] = {
        "endpoint": "fund_adj", "protocol": PROTOCOL, "partitions": partitions,
        "partitionComplete": True, "tradingCalendarVerified": False,
        "observedAt": datetime.now(timezone.utc).isoformat(),
        "contentFingerprint": sha256(json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "anchor": None, "upstreamDataRevision": None, "adjustmentAlgorithmRevision": None,
        "conversionAvailable": False,
    }
    return frame


class TushareFundFactorsMixin:
    def get_fund_adjustment_factors_for_source(
        self, stock_code, upstream_source, *, start_date, end_date, timeout_seconds=15,
    ):
        if upstream_source != "tushare":
            raise ValueError("unsupported Tushare factor source")
        symbol = self._convert_stock_code(stock_code)
        require_cn_etf_symbol(symbol)
        request = TushareHistoryRequest(self, timeout_seconds)
        result = fetch_tushare_fund_adj(
            request.client, symbol, start_date, end_date, before_call=request.before_call,
        )
        request.check_complete()
        return result
