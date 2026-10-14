"""Tushare 六个已接入接口的账户检查；不授予历史数据或现金语义。"""

import requests

from data_provider.tushare_http_client import TushareApiError

# 固定单日/单证券小样本，只检查接口接受情况，不要求样本有记录。
SAMPLES = {
    ("CN", "STOCK", "DAILY_BAR"): ("daily", {"ts_code": "600519.SH", "trade_date": "20250115"}),
    ("HK", "STOCK", "DAILY_BAR"): ("hk_daily", {"ts_code": "00700.HK", "trade_date": "20250115"}),
    ("CN", "ETF", "DAILY_BAR"): ("fund_daily", {"ts_code": "510300.SH", "trade_date": "20250115"}),
    ("CN", "STOCK", "REALTIME_QUOTE"): ("rt_k", {"ts_code": "600519.SH"}),
    ("CN", "STOCK", "CHIP_SUMMARY"): ("cyq_chips", {"ts_code": "600519.SH", "trade_date": "20250115"}),
    ("CN", "ETF", "CASH_DISTRIBUTION"): ("fund_div", {"ts_code": "510300.SH"}),
}


def failure_result(error):
    state, code = "unknown", "upstream_failure"
    if isinstance(error, (requests.Timeout, requests.ConnectionError, TimeoutError)):
        code = "network_failure"
    elif isinstance(error, TushareApiError):
        text = str(error).lower().replace(" ", "")
        if error.status_code == 429 or any(part in text for part in ("每分钟", "每天", "每日", "每小时", "每秒", "访问频率", "访问次数", "频次", "ratelimit", "toomany", "quota")):
            code = "rate_limited"
        elif error.status_code == 401 or any(part in text for part in ("invalidtoken", "token无效", "token不对", "token错误", "token已过期", "token失效", "无效的token")):
            state, code = "credential_invalid", "authentication_failed"
        elif error.api_code == 2002 or (error.api_code == 40203 and any(part in text for part in ("权限", "积分不足"))):
            state, code = "permission_missing", "permission_denied"
    elif isinstance(error, (ValueError, TypeError, AttributeError)):
        code = "invalid_response"
    return {"state": state, "attempted": True, "errorCode": code}


def probe_account_access(context):
    key = context.key
    sample = SAMPLES.get((key["market"], key["assetType"], key["capability"]))
    if sample is None or context.target.get("upstreamSource") != "tushare":
        return {"state": "unknown", "attempted": False, "errorCode": "not_adapted"}
    api = getattr(context.adapter, "_api", None)
    if api is None:
        return {"state": "credential_missing", "attempted": False, "errorCode": "credential_missing"}
    try:
        api.query(sample[0], fields="ts_code", timeout_seconds=context.timeout_seconds, **sample[1])
        return {"state": "allowed", "attempted": True}
    except Exception as error:
        return failure_result(error)
