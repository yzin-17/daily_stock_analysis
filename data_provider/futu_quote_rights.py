"""富途 OpenD 市场行情权限的只读投影。"""

_LIVE_RIGHTS = {"HK": {"LV1", "LV2", "LV3", "SF"}, "US": {"LV1", "LV2", "LV3"}}


def read_quote_rights(context, sdk):
    status, payload = context.get_user_info([sdk.UserInfoField.QOTRIGHT])
    if status != sdk.RET_OK or not isinstance(payload, dict):
        return {}
    return {market: payload.get(market.lower() + "_qot_right") for market in _LIVE_RIGHTS}


def quote_right_state(rights, market):
    right = rights.get(market)
    if isinstance(right, str) and right in _LIVE_RIGHTS.get(market, ()):
        return "allowed"
    if right in ("NO", "BMP"):
        return "permission_missing"
    return "unknown"


def inspect_quote_rights(sdk, host, port):
    context = sdk.OpenQuoteContext(host=host, port=port)
    try:
        rights = read_quote_rights(context, sdk)
        return {market: quote_right_state(rights, market) for market in _LIVE_RIGHTS}
    finally:
        context.close()
