"""退役登记的兼容哨兵，仅供迁移回归与防重门禁；生产不导入。"""

NATIVE_DAILY_ROUTES = {}


def native_daily_route_matches(key, target):
    return False
