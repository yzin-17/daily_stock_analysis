"""TickFlow 单次日线请求与规范化，供来源内部不同传输复用。"""

import pandas as pd
from data_provider.exact_daily_contract import ExactDailyError, normalize_exact_daily


def read_frame(adapter, request, *, source="tickflow", validate_raw=None):
    code, exchange = request.symbol.split(".", 1)
    if exchange not in {"SH", "SZ", "BJ"} or len(code) != 6 or not code.isdigit():
        raise ExactDailyError("invalid_request", "中国内地日线需要完整证券代码")
    client = adapter._get_client()
    if client is None:
        raise ExactDailyError("credential_missing", "TickFlow API Key 未配置")
    payload = client.klines.get(
        request.symbol, period="1d", count=adapter._daily_kline_count(request.start, request.end),
        start_time=adapter._date_to_ms(request.start), end_time=adapter._date_to_ms(request.end, end_of_day=True),
        adjust={"none": "none", "qfq": "forward", "hfq": "backward"}[request.adjustment], as_dataframe=False,
    )
    if validate_raw is not None:
        validate_raw(payload, request, adapter._daily_kline_count(request.start, request.end))
    raw = pd.DataFrame(payload)
    if "date" not in raw.columns:
        timestamp = next((field for field in ("timestamp", "time", "ts") if field in raw.columns), None)
        if timestamp is None:
            raise ExactDailyError("invalid_response", "TickFlow 日线缺少日期")
        return normalize_exact_daily(raw.rename(columns={timestamp: "date"}), start=request.start,
                                     end=request.end, source=source, date_unit="ms")
    return normalize_exact_daily(raw, start=request.start, end=request.end, source=source)
