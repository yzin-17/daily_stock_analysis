"""TickFlow 逐精确功能检查；匿名日线与账号接口共享一个来源身份。"""

ANONYMOUS_URL = "https://free-api.tickflow.org"
ACCOUNT_URL = "https://api.tickflow.org"


def requires_credential(key, target):
    return key.get("capability") != "DAILY_BAR"


def failure_result(error):
    from tickflow._exceptions import AuthenticationError, PermissionError, RateLimitError, ConnectionError, TimeoutError
    state, code = "unknown", "upstream_failure"
    if isinstance(error, AuthenticationError):
        state, code = "credential_invalid", "authentication_failed"
    elif isinstance(error, PermissionError):
        state, code = "permission_missing", "permission_denied"
    elif isinstance(error, RateLimitError):
        code = "rate_limited"
    elif isinstance(error, (ConnectionError, TimeoutError)):
        code = "network_failure"
    elif isinstance(error, (ValueError, TypeError, KeyError)):
        code = "invalid_response"
    return {"state": state, "attempted": True, "errorCode": code}


def probe_account_access(context):
    from tickflow import TickFlow
    key = context.key
    daily = key["capability"] == "DAILY_BAR"
    api_key = "" if daily else context.snapshot.values.get("apiKey", "")
    if not daily and not api_key:
        return {"state": "credential_missing", "attempted": False, "errorCode": "credential_missing"}
    client = TickFlow(api_key=api_key, base_url=ANONYMOUS_URL if daily else ACCOUNT_URL,
                      timeout=context.timeout_seconds, max_retries=0)
    try:
        symbol = "510300.SH" if key["assetType"] == "ETF" else "600519.SH"
        if key["capability"] == "REALTIME_QUOTE":
            value = client.quotes.get(symbols=[symbol])
            if not isinstance(value, list):
                raise ValueError("行情接口响应无效")
        else:
            value = client.klines.get(symbol, period="1d" if daily else key["timeframe"], count=1,
                adjust={"none": "none", "qfq": "forward", "hfq": "backward"}.get(key.get("adjustment"), "none"),
                as_dataframe=False)
            if not isinstance(value, dict) or not isinstance(value.get("timestamp"), list):
                raise ValueError("K线接口响应无效")
        return {"state": "allowed", "attempted": True}
    except Exception as error:
        return failure_result(error)
    finally:
        client.close()
