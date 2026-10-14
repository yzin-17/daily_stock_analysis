"""事件时钟精确比较；时刻字符串保持原字节，不转浮点或截断小数。"""

from datetime import date
import re

_INSTANT = re.compile(
    r'([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})'
    r'(?:\.([0-9]{1,1024}))?(Z|[+-][0-9]{2}:[0-9]{2})'
)


def instant(value: str) -> tuple[int, str]:
    if not isinstance(value, str) or len(value) > 1050:
        raise ValueError('事件时刻无效')
    parts = _INSTANT.fullmatch(value)
    if parts is None:
        raise ValueError('事件时刻无效')
    year, month, day, hour, minute, second = map(int, parts.groups()[:6])
    fraction, zone = parts.groups()[6:]
    calendar_day = date(year, month, day)
    if hour > 23 or minute > 59 or second > 59 or zone == '-00:00':
        raise ValueError('事件时刻无效')
    offset = 0
    if zone != 'Z':
        offset_hour, offset_minute = int(zone[1:3]), int(zone[4:6])
        if offset_hour > 23 or offset_minute > 59:
            raise ValueError('事件时区无效')
        offset = (offset_hour*60 + offset_minute)*60
        if zone[0] == '-':
            offset = -offset
    seconds = (calendar_day.toordinal() - date(1970, 1, 1).toordinal())*86400
    seconds += hour*3600 + minute*60 + second - offset
    return seconds, (fraction or '').ljust(1024, '0')
