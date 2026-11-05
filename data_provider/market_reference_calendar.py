"""华尔街见闻同一发布者的宏观日历；保留各条记录自己的单位。"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .market_reference_projection import http_json, number, require, result, text

ENDPOINT = 'https://api-one-wscn.awtmt.com/apiv1/finance/macrodatas'
ZONE = timezone(timedelta(hours=8))


def interval(query):
    begin = datetime.fromisoformat(query.start).replace(tzinfo=ZONE)
    end = datetime.fromisoformat(query.end).replace(hour=23, minute=59, second=59, tzinfo=ZONE)
    return int(begin.timestamp()), int(end.timestamp())


def project_calendar(payload, query):
    require(isinstance(payload, dict) and payload.get('code') == 20000 and isinstance(payload.get('data'), dict), '日历业务封套无效')
    rows = payload['data'].get('items')
    require(isinstance(rows, list), '日历记录缺失')
    begin, end = interval(query)
    records, seen = [], set()
    for row in rows:
        require(isinstance(row, dict) and type(row.get('id')) is int and row['id'] not in seen, '日历记录身份无效')
        seen.add(row['id'])
        stamp = row.get('public_date')
        require(type(stamp) is int and begin <= stamp <= end, '日历记录越过请求窗口')
        url = text(row.get('uri'))
        require(url is None or urlparse(url).hostname in {'wallstreetcn.com', 'www.wallstreetcn.com'}, '日历原文出处不符')
        record = {'publishedAt': datetime.fromtimestamp(stamp, ZONE).isoformat(), 'country': text(row.get('country')),
                  'title': text(row.get('title')), 'sourceUnit': text(row.get('unit')), 'importance': number(row.get('importance'), True),
                  'originalUrl': url, **{key: text(row.get(key)) for key in ('actual', 'forecast', 'previous', 'revised')}}
        require(record['title'] is not None, '日历标题缺失')
        records.append(record)
    records.sort(key=lambda row: (row['publishedAt'], row['originalUrl'] or '', row['title']))
    start = (query.page - 1) * query.pageSize
    return result(query, 'wallstreetcn', 'wallstreetcn-macro-calendar', 'wallstreetcn-macro-calendar-v1',
                  'https://wallstreetcn.com/calendar', records[start:start + query.pageSize], total=len(records),
                  has_more=start + query.pageSize < len(records),
                  notes=('公布时间为来源日历计划时间；预期、前值及修正值保持来源原文，不合并不同国家和单位。',))


def read_calendar(query, context):
    begin, end = interval(query)
    return project_calendar(http_json(ENDPOINT, {'start': begin, 'end': end}, {'User-Agent': 'Mozilla/5.0'},
                                     context.timeout_seconds), query)
