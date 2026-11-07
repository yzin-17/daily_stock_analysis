"""单标的经济价格候选只读采集；不创建Store、不改配置、不授予价格资格。"""

import argparse
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
import hashlib
import json
import re
import sqlite3
from types import SimpleNamespace
from zoneinfo import ZoneInfo


CLOCK = ZoneInfo('Asia/Shanghai')
BASE = 'https://fuyao.aicubes.cn'


def plan(symbol, start, end, event_history=False):
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol):
        raise ValueError('完整证券代码无效')
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not 0 <= (last - first).days <= 31:
        raise ValueError('仅允许最多32天的固定资格窗口')
    if last >= datetime.now(CLOCK).date():
        raise ValueError('资格采集仅覆盖已结束的历史交易日期')
    millis = lambda day: int(datetime.combine(day, time(), CLOCK).timestamp() * 1000)
    return [
        ('corporate_actions', '/api/a-share/corporate-actions/adjustment-factors',
         {'thscode': symbol} if event_history else {'thscode': symbol, 'from': start, 'to': end}),
        ('raw_prices', '/api/a-share/prices/historical',
         {'thscode': symbol, 'interval': '1d', 'start': millis(first),
          'end': millis(last + timedelta(days=1)) - 1, 'adjust': 'none'}),
    ]


def number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('来源数值类型无效')
    parsed = Decimal(str(value))
    if not parsed.is_finite() or parsed < 0 or (positive and parsed == 0):
        raise ValueError('来源数值范围无效')
    return parsed


def market_day(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('来源日期类型无效')
    clock = datetime.fromtimestamp(value / 1000, CLOCK)
    if clock.time() != time():
        raise ValueError('来源日期不是上海零点')
    return clock.date().isoformat()


def validate(kind, data, symbol, start, end, event_history=False):
    if not isinstance(data, dict) or not isinstance(data.get('item'), list):
        raise ValueError('来源缺少明确数组')
    rows, seen = data['item'], set()
    if kind == 'corporate_actions':
        if data.get('thscode') != symbol or data.get('ticker') != symbol[:6]:
            raise ValueError('公司行动响应证券不匹配')
        for row in rows:
            if row.get('ticker') != symbol[:6]:
                raise ValueError('公司行动行证券不匹配')
            day = market_day(row['ex_date_ms'])
            if day in seen or (not event_history and not start <= day <= end):
                raise ValueError('公司行动日期重复或越界')
            seen.add(day)
            number(row['dividend_per_share'])
            number(row['per_share_bonus'])
        window_count = sum(start <= day <= end for day in seen)
        return {'rowCount': len(rows), 'windowEventCount': window_count,
                'eventFreeCandidate': window_count == 0,
                'eventScope': 'all_history' if event_history else 'requested_interval',
                'economicQualification': 'not_verified'}
    if not rows:
        raise ValueError('原始价格为空')
    previous = None
    for row in rows:
        day = market_day(row['date_ms'])
        if day in seen or not start <= day <= end or (previous and day <= previous):
            raise ValueError('日线日期重复、越界或乱序')
        previous = day
        seen.add(day)
        opening, high, low, close = [number(row[key], positive=True) for key in
                                     ('open_price', 'high_price', 'low_price', 'close_price')]
        if low > min(opening, close) or high < max(opening, close) or low > high:
            raise ValueError('日线价格关系无效')
        number(row['volume'])
        number(row['turnover'])
    return {'rowCount': len(rows), 'dates': sorted(seen), 'identityBasis': 'single_request_bound',
            'economicQualification': 'not_verified'}


def collect(symbol, start, end, key, fetch, event_history=False):
    result = {'symbol': symbol, 'start': start, 'end': end, 'sourceRequests': 0,
              'feeStatus': 'unverified', 'economicQualification': 'not_verified', 'reads': []}
    for kind, endpoint, params in plan(symbol, start, end, event_history):
        result['sourceRequests'] += 1
        entry = {'kind': kind, 'endpoint': BASE + endpoint, 'parameters': params,
                 'startedAt': datetime.now(timezone.utc).isoformat()}
        result['reads'].append(entry)
        try:
            response = fetch(BASE + endpoint, params=params, headers={'X-api-key': key},
                             timeout=10, allow_redirects=False)
            entry['httpStatus'] = response.status_code
            raw = response.content
            entry['responseSha256'] = hashlib.sha256(raw).hexdigest()
            if response.status_code != 200 or len(raw) > 2_000_000:
                raise ValueError('来源HTTP状态或响应长度无效')
            if key and key in raw.decode('utf-8', errors='replace'):
                raise ValueError('来源响应包含凭据，禁止导出')
            wire = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(wire, dict) or type(wire.get('code')) is not int or wire['code'] != 0:
                entry['businessCode'] = wire.get('code') if isinstance(wire, dict) else None
                raise ValueError('来源业务响应失败')
            entry['response'] = wire
            entry['validation'] = validate(kind, wire['data'], symbol, start, end, event_history)
            entry['completedAt'] = datetime.now(timezone.utc).isoformat()
        except Exception as error:
            entry['errorType'] = type(error).__name__
            result['transportAndProtocolPassed'] = False
            return result
    result['transportAndProtocolPassed'] = True
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol', required=True)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--database', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--event-history', action='store_true',
                        help='以完整事件流核对窗口缺事件，不能把3002当作合法空数组')
    args = parser.parse_args()
    requests = plan(args.symbol, args.start, args.end, args.event_history)
    if not args.execute:
        print(json.dumps({'plan': requests, 'sourceRequests': 0}, ensure_ascii=False))
        return
    from src.services.thesis_ledger_control import ThesisLedgerControlStore
    import requests as http

    @contextmanager
    def connect():
        connection = sqlite3.connect('file:' + args.database + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def state():
        with connect() as connection:
            row = connection.execute('SELECT * FROM thesis_ledger_provider_config '
                                     'WHERE provider_id=?', ('hithink',)).fetchone()
            if row is None or row['enabled'] != 1:
                raise ValueError('现有HiThink未启用，停止资格采集')
            return tuple(row)

    before = state()
    # 复用既有解密读者；不实例化含建表/凭据轮换副作用的Store。
    snapshot = ThesisLedgerControlStore.provider_credential_snapshot(
        SimpleNamespace(_connect=connect), 'hithink')
    if snapshot.method != 'api_key' or not snapshot.values.get('apiKey'):
        raise ValueError('现有HiThink凭据不可用')
    result = collect(args.symbol, args.start, args.end, snapshot.values['apiKey'], http.get,
                     args.event_history)
    result['providerStateUnchanged'] = before == state()
    if not result['providerStateUnchanged']:
        result['transportAndProtocolPassed'] = False
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
