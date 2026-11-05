"""富途历史日线：显式复权、完整分页，失败不返回前缀数据。"""

from .exact_daily_contract import normalize_exact_daily
from .futu_history import read_futu_history


def read_futu_daily(sdk, host, port, request):
    raw, zone = read_futu_history(sdk, host, port, request, timeframe="1d")
    return normalize_exact_daily(raw, start=request["start"], end=request["end"], source="futu",
        columns={"time_key": "date", "turnover": "amount"}, timezone=zone)
