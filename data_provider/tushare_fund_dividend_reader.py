"""基金分红按标的精确读取，不假设未证明的历史分页协议。"""

from datetime import datetime, timezone
from hashlib import sha256
import json

import pandas as pd

from .tushare_fund_dividends import DATE_FIELDS, normalize_tushare_fund_dividends, validate_fund_dividend_scope
from .tushare_history_request import TushareHistoryRequest

FIELDS = ",".join(("ts_code", "div_proc", "div_cash", *DATE_FIELDS))


def fetch_tushare_fund_dividends(
    api, symbol, *, instrument_type, currency, start, end, before_call, maximum_rows=2000,
):
    validate_fund_dividend_scope(symbol, instrument_type, currency, start, end)
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= 10000:
        raise ValueError("fund_div invalid local response budget")
    before_call()
    frame = api.fund_div(ts_code=symbol, fields=FIELDS)
    if not isinstance(frame, pd.DataFrame) or len(frame) > maximum_rows:
        raise ValueError("fund_div response exceeds local budget or has invalid shape")
    # JSON 的可空字段可能在 DataFrame 中转成 NaN；摘要恢复明确的 null。
    records = frame.astype(object).where(pd.notna(frame), None).to_dict("records")
    fingerprint = sha256(json.dumps(records, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode()).hexdigest()
    result = normalize_tushare_fund_dividends(
        frame, symbol, instrument_type=instrument_type, currency=currency, start=start, end=end,
        observed_at=datetime.now(timezone.utc), provider_revision=f"tushare-fund-div-content-v1:{fingerprint}",
    )
    result["providerRevision"] = f"tushare-fund-div-content-v1:{fingerprint}"
    result["retrieval"] = {
        "endpoint": "fund_div", "queryKind": "symbol-history", "requestCount": 1,
        "returnedRows": len(frame), "localMaximumRows": maximum_rows,
        "contentFingerprint": fingerprint, "upstreamDataRevision": None,
        "upstreamPaginationVerified": False,
    }
    return result


class TushareFundDividendsMixin:
    def get_fund_dividends_for_source(
        self, symbol, upstream_source, *, instrument_type, currency, start_date, end_date, timeout_seconds=15,
    ):
        if upstream_source != "tushare":
            raise ValueError("unsupported fund_div source")
        validate_fund_dividend_scope(symbol, instrument_type, currency, start_date, end_date)
        request = TushareHistoryRequest(self, timeout_seconds)
        result = fetch_tushare_fund_dividends(
            request.client, symbol, instrument_type=instrument_type, currency=currency,
            start=start_date, end=end_date, before_call=request.before_call,
        )
        request.check_complete()
        return result
