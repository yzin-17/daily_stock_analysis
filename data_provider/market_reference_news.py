"""东财全球快讯的原文身份及来源游标。"""

from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4

from .market_reference_projection import http_json, require, result, text

ENDPOINT = 'https://np-weblist.eastmoney.com/comm/web/getFastNewsList'


def project_news(payload, query):
    require(isinstance(payload, dict) and str(payload.get('code')) == '1', '快讯业务请求失败')
    data = payload.get('data')
    require(isinstance(data, dict) and isinstance(data.get('fastNewsList'), list), '快讯列表缺失')
    require(data.get('size') == query.pageSize, '快讯页大小不符')
    records = []
    for row in data['fastNewsList']:
        code = row.get('code')
        require(isinstance(code, str) and re.fullmatch(r'\d{16,24}', code), '快讯来源身份缺失')
        stamp = datetime.fromisoformat(row['showTime']).replace(tzinfo=timezone(timedelta(hours=8)))
        require(row.get('title'), '快讯标题缺失')
        records.append({'publishedAt': stamp.isoformat(), 'title': text(row['title']),
                        'summary': text(row.get('summary')), 'originalUrl': f'https://finance.eastmoney.com/a/{code}.html'})
    cursor = data.get('sortEnd') if records else None
    if cursor is not None:
        require(isinstance(cursor, str) and re.fullmatch(r'[0-9A-Za-z_.:-]+', cursor) and cursor != query.cursor, '来源游标无效')
    return result(query, 'akshare', 'eastmoney', 'eastmoney-global-news-reference-v1', 'https://kuaixun.eastmoney.com/',
                  records, has_more=cursor is not None, next_cursor=cursor,
                  notes=('原文地址按已核对的来源文章标识生成；只覆盖来源当前可翻页窗口，不声明快讯全历史。',))


def read_news(query, context):
    params = {'client': 'web', 'biz': 'web_724', 'fastColumn': '102', 'sortEnd': query.cursor or '',
              'pageSize': str(query.pageSize), 'req_trace': str(uuid4())}
    return project_news(http_json(ENDPOINT, params, {'User-Agent': 'Mozilla/5.0',
        'Referer': 'https://kuaixun.eastmoney.com/'}, context.timeout_seconds), query)
