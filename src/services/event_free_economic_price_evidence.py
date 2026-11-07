"""把已审核的完整原始响应编译为窄窗口证据；任何窗口内公司行动均拒绝。"""

from datetime import datetime, time, timedelta
import hashlib
import json
import re
from zoneinfo import ZoneInfo

DOCUMENT_SHA256 = '6bb1e9411bdd9f5cc1989b1a1147ef38378141f9acf7ba0075057687fe36db83'
FORMAT = 'event-free-economic-price-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def qualify(candidate, documentation, expected_dates, *, capture_sha256, qualified_at):
    import math
    from decimal import Decimal

    def instant(value):
        clock = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if clock.utcoffset() is None:
            raise ValueError('来源时钟缺少时区')
        return clock

    if hashlib.sha256(documentation).hexdigest() != DOCUMENT_SHA256:
        raise ValueError('官方协议版本未核验')
    if candidate.get('transportAndProtocolPassed') is not True or candidate.get('providerStateUnchanged') is not True:
        raise ValueError('来源响应或控制状态未通过')
    symbol, start, end = (candidate[k] for k in ('symbol', 'start', 'end'))
    if not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)', symbol) or not re.fullmatch(r'[a-f0-9]{64}', capture_sha256):
        raise ValueError('仅覆盖规范境内股票身份')
    reads = candidate['reads']
    if len(reads) != 2 or [r['kind'] for r in reads] != ['corporate_actions', 'raw_prices']:
        raise ValueError('必须保留一份完整事件流及一份原始价格')
    actions, prices = reads
    for read in reads:
        if read['httpStatus'] != 200 or type(read['response'].get('code')) is not int or read['response']['code'] != 0:
            raise ValueError('HTTP或业务响应失败')
        if read['parameters']['thscode'] != symbol:
            raise ValueError('请求身份不一致')
        if instant(read['completedAt']) > instant(qualified_at):
            raise ValueError('来源响应晚于资格核对时刻')
    if actions['endpoint'] != 'https://fuyao.aicubes.cn/api/a-share/corporate-actions/adjustment-factors' or actions['parameters'] != {'thscode': symbol}:
        raise ValueError('必须是无日期过滤的完整事件流')
    if prices['endpoint'] != 'https://fuyao.aicubes.cn/api/a-share/prices/historical' or prices['parameters'].get('adjust') != 'none' or prices['parameters'].get('interval') != '1d':
        raise ValueError('必须是同标的原始日线')
    clock = ZoneInfo('Asia/Shanghai')
    if instant(qualified_at).astimezone(clock).date().isoformat() <= end:
        raise ValueError('资格核查只能使用已结束日期的完整日线')
    first = datetime.fromisoformat(start).replace(tzinfo=clock)
    last = datetime.fromisoformat(end).replace(tzinfo=clock) + timedelta(days=1)
    if prices['parameters'].get('start') != int(first.timestamp()*1000) or prices['parameters'].get('end') != int(last.timestamp()*1000)-1:
        raise ValueError('来源请求未精确固定原价格窗口')
    market_date = lambda value: datetime.fromtimestamp(value / 1000, clock).date().isoformat()
    action_data = actions['response']['data']
    if action_data.get('thscode') != symbol or action_data.get('ticker') != symbol[:6] or not isinstance(action_data.get('item'), list) or not action_data['item']:
        raise ValueError('完整历史流须有可验证的证券及真实记录')
    event_dates = []
    for row in action_data['item']:
        if row.get('ticker') != symbol[:6]:
            raise ValueError('公司行动行身份不一致')
        day = market_date(row['ex_date_ms'])
        if start <= day <= end:
            raise ValueError('窗口含公司行动，窄路径不支持')
        for name in ('dividend_per_share', 'per_share_bonus'):
            value = row[name]
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError('公司行动数值无效')
        event_dates.append(day)
    if len(set(event_dates)) != len(event_dates) or event_dates != sorted(event_dates, reverse=True):
        raise ValueError('完整事件流顺序或唯一性不符合协议')
    bars = []
    for row in prices['response']['data']['item']:
        stamp = datetime.fromtimestamp(row['date_ms']/1000, clock)
        if (stamp.hour, stamp.minute, stamp.second, stamp.microsecond) != (0, 0, 0, 0):
            raise ValueError('原始日线时区无效')
        values = [row[k] for k in ['open_price', 'high_price', 'low_price', 'close_price', 'volume', 'turnover']]
        if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError('必须有正价格和真实成交量额')
        opening, high, low, close, volume, amount = values
        if low > min(opening, close) or high < max(opening, close) or low > high:
            raise ValueError('原始OHLC关系无效')
        bars.append({'timestamp': stamp.isoformat(timespec='milliseconds'), 'open': opening,
                     'high': high, 'low': low, 'close': close, 'volume': volume, 'amount': amount,
                     'completionStatus': 'complete', 'availableAt': qualified_at})
    dates = [datetime.fromisoformat(b['timestamp']).date().isoformat() for b in bars]
    if not dates or dates != expected_dates or expected_dates != sorted(set(expected_dates)) or dates[0] != start or dates[-1] != end:
        raise ValueError('价格必须严格覆盖合格日历的原固定窗口')
    source = {'providerId': 'hithink', 'upstreamSource': 'hithink-financial-api'}
    proof = {'kind': 'verified-event-free-window', 'eventDates': event_dates,
             'fullEventCount': len(event_dates), 'windowEventCount': 0,
             'documentationSha256': DOCUMENT_SHA256,
             'corporateActionContentHash': digest(actions['response']),
             'rawPriceContentHash': digest(prices['response']),
             'captureSha256': capture_sha256}
    # 窗口内没有公司行动，经济收益为同份raw收盘价之比；单位因子不是供应商qfq猜测。
    raw_return = Decimal(str(bars[-1]['close'])) / Decimal(str(bars[0]['close'])) - 1
    return {'format': FORMAT, 'symbol': symbol, 'market': 'CN', 'assetType': 'STOCK',
            'currency': 'CNY', 'start': start, 'end': end, 'source': source,
            'qualifiedAt': qualified_at, 'anchorDate': end,
            'bars': bars, 'qualification': proof, 'independentReturn': str(raw_return)}
