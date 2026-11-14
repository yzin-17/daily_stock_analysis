"""HiThink 原生代码表的有界完整分页；保留生命周期字段原文。"""

from datetime import date, datetime, timezone
import re
import requests

URL = 'https://fuyao.aicubes.cn/api/meta/tickers/list'


class CatalogSourceError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def read_catalog(api_key, *, http_get=requests.get):
    rows, timestamp = [], None
    for page in range(2):
        response = http_get(URL, params={'asset_type': 'a-share,fund-etf', 'limit': 10000, 'offset': page * 10000},
                            headers={'X-api-key': api_key}, timeout=8, allow_redirects=False)
        if response.status_code != 200:
            raise CatalogSourceError('upstream_unavailable')
        body = response.json()
        if not isinstance(body, dict) or type(body.get('code')) is not int:
            raise CatalogSourceError('invalid_response')
        if body.get('code') != 0:
            raise CatalogSourceError({2001: 'authentication_failed', 2003: 'permission_denied', 4001: 'rate_limited'}.get(body.get('code'), 'upstream_failure'))
        data = body.get('data')
        if (not isinstance(data, dict) or not isinstance(data.get('item'), list) or type(data.get('timestamp')) is not int
                or data['timestamp'] <= 0 or any(not isinstance(i, dict) for i in data['item'])):
            raise CatalogSourceError('invalid_response')
        if timestamp is not None and timestamp != data['timestamp']:
            raise CatalogSourceError('snapshot_changed')
        timestamp = data['timestamp']
        rows.extend(data['item'])
        if len(data['item']) < 10000:
            if not rows:
                raise CatalogSourceError('empty_response')
            return {'items': rows, 'sourceTimestamp': timestamp, 'pages': page + 1,
                    'observedAt': datetime.now(timezone.utc).isoformat()}
    raise CatalogSourceError('insufficient_coverage')


def project_catalog(raw, day):
    if date.fromisoformat(day).isoformat() != day:
        raise CatalogSourceError('invalid_request')
    items, seen = [], set()
    for row in raw['items']:
        symbol, kind = row.get('thscode'), row.get('asset_type')
        if (not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol)
                or row.get('exchange') != symbol.split('.')[1] or row.get('currency') != 'CNY'
                or kind not in {'a-share', 'fund-etf'} or symbol in seen):
            raise CatalogSourceError('invalid_response')
        seen.add(symbol)
        listed, last = row.get('list_date'), row.get('last_trade_date')
        try:
            if not listed or date.fromisoformat(listed).isoformat() != listed:
                raise ValueError('缺少上市日期')
            if last and (date.fromisoformat(last).isoformat() != last or last < listed):
                raise ValueError('最后交易日期无效')
        except (ValueError, TypeError):
            raise CatalogSourceError('lifecycle_incomplete') from None
        if listed <= day and (last is None or day <= last):
            items.append({'symbol': symbol, 'assetType': 'STOCK' if kind == 'a-share' else 'ETF',
                          'name': symbol, 'listedOn': listed})
    for suffix in ('.SH', '.SZ', '.BJ'):
        if not any(i['assetType'] == 'STOCK' and i['symbol'].endswith(suffix) for i in items):
            raise CatalogSourceError('insufficient_coverage')
    if not any(i['assetType'] == 'ETF' for i in items):
        raise CatalogSourceError('insufficient_coverage')
    return sorted(items, key=lambda i: i['symbol'])
