"""基金净值日期契约，不表示净值公告可见时间。"""

import re
import math
from datetime import datetime, timezone


def nav_datetime(value) -> datetime:
    text = str(value).strip()
    if not re.match(r'^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:$|[T ])', text):
        raise ValueError('Provider 净值日期格式非法')
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Provider 净值日期格式非法') from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_nav_history(rows) -> None:
    previous = None
    for date_value, nav_value in rows:
        current = nav_datetime(date_value)
        if previous is not None and current <= previous:
            raise ValueError('Provider 净值历史必须严格升序')
        previous = current
        try:
            nav = float(nav_value)
        except (TypeError, ValueError) as exc:
            raise ValueError('Provider 响应字段 unitNav 非法') from exc
        if isinstance(nav_value, bool) or not math.isfinite(nav) or nav <= 0:
            raise ValueError('Provider 响应字段 unitNav 非法')
