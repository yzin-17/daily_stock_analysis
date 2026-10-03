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


def select_nav_rows(frame, *, start=None, end=None, limit=None, latest_only=False):
    """先校验完整来源序列，再选出可交给当前请求的净值行。"""
    date_column = next(
        (column for column in ('净值日期', '日期', 'date', 'nav_date') if column in frame.columns),
        None,
    )
    if date_column is None:
        raise ValueError('Provider 净值响应缺少日期字段')
    dated = [
        (position, nav_datetime(row.get(date_column)), str(row.get(date_column)).strip()[:10])
        for position, (_, row) in enumerate(frame.iterrows())
    ]
    selected = [
        item for item in dated
        if (start is None or item[2] >= start) and (end is None or item[2] <= end)
    ]
    if latest_only and selected:
        selected = [max(selected, key=lambda item: item[1])]
    elif limit is not None:
        selected = selected[-limit:]
    if not selected:
        raise ValueError('Provider 净值未覆盖请求窗口')
    positions = [position for position, _, _ in selected]
    if positions == list(range(len(dated))):
        return frame
    if not hasattr(frame, 'iloc'):
        raise ValueError('Provider 净值响应无法按请求窗口截取')
    return frame.iloc[positions].copy()
