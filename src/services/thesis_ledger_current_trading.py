"""当前内地证券停牌观察；独立于历史价格限制、费用和结算规则。"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from math import ceil
import re
from threading import Lock
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import requests

from .thesis_ledger_sse_halt_query import read_halt_rows, halt_interval

_lock = Lock()
_cache = {}
_CN = ZoneInfo('Asia/Shanghai')


def _fetch(url, params):
    response = requests.get(url, params=params, headers={
        'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.sse.com.cn/'},
        timeout=12, allow_redirects=False)
    response.raise_for_status()
    if response.status_code != 200:
        raise ValueError('停牌查询未返回完整响应')
    return response.url, response.content


def _stock(day, fetch):
    pages, rows, count = [], [], None
    for number in range(1, 201):
        ref, raw = fetch('https://datacenter-web.eastmoney.com/api/data/v1/get', {
            'sortColumns': 'SUSPEND_START_DATE', 'sortTypes': '-1', 'pageSize': '500',
            'pageNumber': str(number), 'reportName': 'RPT_CUSTOM_SUSPEND_DATA_INTERFACE',
            'columns': 'ALL', 'source': 'WEB', 'client': 'WEB',
            'filter': f'(MARKET="全部")(DATETIME=\'{day}\')'})
        body = json.loads(raw)
        result = body.get('result')
        if body.get('success') is not True or body.get('code') != 0 or not isinstance(result, dict):
            raise ValueError('停牌列表查询失败')
        total, data = result.get('count'), result.get('data')
        if (type(total) is not int or not 0 <= total <= 100000
                or count is not None and total != count or not isinstance(data, list)
                or result.get('pages') != max(1, ceil(total / 500))
                or len(data) != min(500, max(0, total - (number - 1) * 500))):
            raise ValueError('停牌列表分页不完整')
        count = total
        rows.extend(data)
        pages.append({'reference': ref, 'sha256': sha256(raw).hexdigest()})
        if number >= result['pages']:
            break
    if len({r['SECUCODE'] for r in rows}) != len(rows):
        raise ValueError('停牌列表存在重复证券')
    for row in rows:
        if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', row['SECUCODE']):
            raise ValueError('停牌证券身份无效')
    return rows, pages


def _etf(symbol, day, fetch):
    pages, originals = [], {}
    for number in range(1, 1001):
        ref, raw = fetch('https://query.sse.com.cn/sseQuery/commonSoaQuery.do', {
            'isPagination': 'true', 'sqlId': 'SSE_PL_JYTS_TFPXX_JJ',
            'secCode': symbol.split('.')[0], 'stopReason': '',
            'order': 'startStopDate|desc,secCode|asc', 'startDate': '19901219',
            'endDate': day.replace('-', ''), 'pageHelp.pageSize': '25',
            'pageHelp.pageNo': str(number), 'pageHelp.beginPage': str(number),
            'pageHelp.cacheSize': '1', 'pageHelp.endPage': str(number)})
        digest = sha256(raw).hexdigest()
        pages.append(SimpleNamespace(reference=ref, sha256=digest))
        originals[digest] = raw
        total = json.loads(raw)['pageHelp']['total']
        if type(total) is not int or not 0 <= total <= 25000:
            raise ValueError('基金停牌分页总数无效')
        if number >= max(1, ceil(total / 25)):
            break
    rows, _ = read_halt_rows(pages, symbol, day, originals.__getitem__)
    return rows, [vars(p) for p in pages]


def current_trading(symbol, asset_type, *, now=None, fetch=_fetch, sz_etf_reader=None):
    now = now or datetime.now(timezone.utc)
    day = now.astimezone(_CN).date().isoformat()
    result = {'symbol': symbol, 'assetType': asset_type, 'day': day,
              'observedAt': now.isoformat(), 'state': 'unknown', 'evidence': {}}
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol) or asset_type not in {'STOCK', 'ETF'}:
        return result
    if asset_type == 'ETF' and not symbol.endswith('.SH'):
        if symbol.endswith('.SZ') and sz_etf_reader is not None:
            return sz_etf_reader(symbol)
        result['evidence'] = {'reason': 'current_status_not_covered'}
        return result
    try:
        # 同一轮多个股票共用完整市场列表；短缓存保留原抓取时钟。
        key = (asset_type, day, symbol if asset_type == 'ETF' else '*', fetch)
        with _lock:
            saved = _cache.get(key)
            if not saved or not 0 <= (now - saved[0]).total_seconds() < 5:
                rows, pages = _stock(day, fetch) if asset_type == 'STOCK' else _etf(symbol, day, fetch)
                saved = (now, rows, pages)
                _cache.clear()
                _cache[key] = saved
        observed, rows, pages = saved
        suspended = False
        for row in rows:
            if asset_type == 'STOCK':
                if row['SECUCODE'] != symbol:
                    continue
                begin = datetime.fromisoformat(row['SUSPEND_START_TIME']).replace(tzinfo=_CN)
                end = datetime.fromisoformat(row['SUSPEND_END_TIME']).replace(tzinfo=_CN) if row.get('SUSPEND_END_TIME') else None
                if begin.date().isoformat() > day or end and end < begin:
                    raise ValueError('停牌区间无效')
                suspended |= begin <= now and (end is None or now < end)
            else:
                interval = halt_interval(row, day)
                if interval:
                    suspended |= datetime.fromisoformat(interval['startedAt']) <= now < datetime.fromisoformat(interval['endedAt'])
        result.update(state='suspended' if suspended else 'normal', observedAt=observed.isoformat(),
                      evidence={'provider': 'eastmoney' if asset_type == 'STOCK' else 'sse',
                                'pages': pages, 'complete': True})
    except (requests.RequestException, ValueError, KeyError, TypeError):
        result['evidence'] = {'reason': 'current_status_unavailable'}
    return result
