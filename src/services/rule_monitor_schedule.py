"""监测执行计划；默认实际24小时间隔，其余使用标准五段Cron。"""

from datetime import datetime
from zoneinfo import ZoneInfo

from croniter import croniter

DEFAULT_SCHEDULE = "@every24h"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def next_occurrence(expression, timezone, after):
    zone = ZoneInfo(timezone)
    if expression == DEFAULT_SCHEDULE:
        return after + 86400
    if len(expression) > 120 or len(expression.split()) != 5:
        raise ValueError("执行时间必须是五段Cron")
    start = datetime.fromtimestamp(after, zone)
    result = croniter(expression, start, max_years_between_matches=5).get_next(float)
    if result <= after:
        raise ValueError("执行时间必须在未来")
    return result
