"""Tushare 积分与独立接口开通范围；积分来自用户填写。"""

INDEPENDENT_APIS = frozenset({"hk_daily", "rt_k"})
MINIMUM_POINTS = {"daily": 120, "fund_daily": 2000, "fund_div": 400, "cyq_chips": 5000}


def parse_account_level(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"points", "independentApis"}:
        raise ValueError("账号等级格式无效")
    points = value.get("points")
    apis = value.get("independentApis", [])
    if not isinstance(points, int) or isinstance(points, bool) or not 0 <= points <= 9007199254740991:
        raise ValueError("账号积分必须是非负安全整数")
    if not isinstance(apis, list) or any(not isinstance(api, str) or api not in INDEPENDENT_APIS for api in apis):
        raise ValueError("独立接口权限无效")
    return {"points": points, "independentApis": sorted(set(apis))}


def excluded_access(key, level):
    if level is None:
        return None
    from data_provider.tushare_account_access import SAMPLES

    sample = SAMPLES.get((key["market"], key["assetType"], key["capability"]))
    if sample is None:
        return None
    api = sample[0]
    reason = None
    if api in INDEPENDENT_APIS and api not in level["independentApis"]:
        reason = "independent_permission_not_enabled"
    elif api in MINIMUM_POINTS and level["points"] < MINIMUM_POINTS[api]:
        reason = "account_level_insufficient"
    if reason:
        return {"state": "not_included", "attempted": False, "errorCode": reason}
    return None
