"""RQData 单基金分红读取，身份及币种必须由调用方核验。"""

from datetime import datetime, timezone
from hashlib import sha256
import json

import pandas as pd

from .rqdata_fund_dividends import normalize_rqdata_fund_dividends
from .rqdata_fund_event_contract import validate_fund_event_scope


def fetch_rqdata_fund_dividends(
    api, symbol, *, query_fund_code, instrument_type, currency, start, end,
    before_call, after_call, maximum_rows=2000,
):
    validate_fund_event_scope(symbol, query_fund_code, instrument_type, start, end)
    if currency not in {"CNY", "HKD", "USD"}:
        raise ValueError("fund dividend requires verified currency")
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= 10000:
        raise ValueError("fund dividend invalid local response budget")
    if not callable(before_call) or not callable(after_call):
        raise ValueError("fund dividend requires budget callbacks")
    before_call()
    frame = api.fund.get_dividend(query_fund_code, market="cn")
    after_call()
    if not isinstance(frame, pd.DataFrame) or len(frame) > maximum_rows:
        raise ValueError("fund dividend response invalid or exceeds local row budget")
    payload = {"queryFundCode": query_fund_code, "endpoint": "fund.get_dividend",
               "columns": list(frame.columns), "indexNames": list(frame.index.names),
               "index": list(frame.index),
               "records": frame.astype(object).where(pd.notna(frame), None).to_dict("records")}
    fingerprint = sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False,
    ).encode()).hexdigest()
    result = normalize_rqdata_fund_dividends(
        frame, symbol, query_fund_code=query_fund_code, instrument_type=instrument_type, currency=currency,
        start=start, end=end, observed_at=datetime.now(timezone.utc),
        provider_revision=f"rqdata-fund-dividend-content-v1:{fingerprint}",
    )
    result["retrieval"] = {
        "endpoint": "fund.get_dividend", "queryFundCode": query_fund_code, "requestCount": 1,
        "returnedRows": len(frame), "localMaximumRows": maximum_rows, "contentFingerprint": fingerprint,
        "upstreamDataRevision": None, "upstreamPaginationVerified": False,
    }
    after_call()
    return result
