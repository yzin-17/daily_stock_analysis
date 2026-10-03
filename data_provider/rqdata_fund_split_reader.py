"""RQData 单基金拆分读取；账号初始化及传输超时由外部已配置客户端负责。"""

from datetime import datetime, timezone
from hashlib import sha256
import json

import pandas as pd

from .rqdata_fund_splits import normalize_rqdata_fund_splits
from .rqdata_fund_event_contract import validate_fund_event_scope


def fetch_rqdata_fund_splits(
    api, symbol, *, query_fund_code, instrument_type, start, end,
    before_call, after_call, maximum_rows=2000,
):
    """单次精确请求；回调检查调用预算，不宣称能够中断 SDK 内部阻塞。"""
    validate_fund_event_scope(symbol, query_fund_code, instrument_type, start, end)
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= 10000:
        raise ValueError("fund split invalid local response budget")
    if not callable(before_call) or not callable(after_call):
        raise ValueError("fund split requires budget callbacks")
    before_call()
    frame = api.fund.get_split(query_fund_code, market="cn")
    after_call()
    if not isinstance(frame, pd.DataFrame) or len(frame) > maximum_rows:
        raise ValueError("fund split response invalid or exceeds local row budget")
    # 索引承载除权日，必须与原始列共同进入内容摘要，不能只散列 records。
    payload = {"queryFundCode": query_fund_code, "endpoint": "fund.get_split",
               "columns": list(frame.columns), "indexNames": list(frame.index.names),
               "index": list(frame.index),
               "records": frame.astype(object).where(pd.notna(frame), None).to_dict("records")}
    fingerprint = sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False,
    ).encode()).hexdigest()
    result = normalize_rqdata_fund_splits(
        frame, symbol, query_fund_code=query_fund_code, instrument_type=instrument_type,
        start=start, end=end, observed_at=datetime.now(timezone.utc),
        provider_revision=f"rqdata-fund-split-content-v1:{fingerprint}",
    )
    result["retrieval"] = {
        "endpoint": "fund.get_split", "queryFundCode": query_fund_code, "requestCount": 1,
        "returnedRows": len(frame), "localMaximumRows": maximum_rows, "contentFingerprint": fingerprint,
        "upstreamDataRevision": None, "upstreamPaginationVerified": False,
    }
    after_call()
    return result
