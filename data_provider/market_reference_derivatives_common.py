"""衍生品协议的数值、来源时间及当前资料投影。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import re

from .market_reference_projection import number, require
from .market_reference_public_common import PublicRead, paged, source_result, unique
from .market_reference_derivatives_sources import SOURCES

CN = timezone(timedelta(hours=8))


def price(value):
    parsed = number(value)
    return None if parsed is None or Decimal(parsed) == 0 else parsed


def count(value):
    return number(value, integer=True)


def clock(value):
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is None, '来源时间不应含未核对的时区')
    return stamp.replace(tzinfo=CN).isoformat(timespec='seconds')


def text(content):
    try:
        return content.decode('utf-8-sig')
    except UnicodeError:
        return content.decode('gb18030')


def slice_result(query, records, url, *, notes):
    return paged(query, records, url, notes=notes, source=SOURCES[query.dataset])


def direct_result(query, records, url, *, total=None, has_more=False, notes):
    return source_result(query, records, url, total=total, has_more=has_more,
                         notes=notes, source=SOURCES[query.dataset])


def sina_values(content, requested):
    parsed = re.findall(r'var\s+hq_str_([A-Za-z0-9_]+)="([^"\r\n]*)"\s*;', content.decode('gb18030'))
    require(len(parsed) == len({key for key, _ in parsed}) and set(key for key, _ in parsed) == set(requested),
            '新浪返回变量缺失、重复或不是请求的合约')
    return {key: value.split(',') if value else [] for key, value in parsed}
