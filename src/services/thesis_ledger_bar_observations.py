"""有界 K 线观察投影；观察时钟变化不构成价格变化证据。"""

from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math


FIELDS = ('open', 'high', 'low', 'close', 'volume', 'amount')
STOP_CODES = frozenset({'rate_limited', 'rate_limited_global', 'quota_unavailable',
                        'permission_denied', 'authentication_failed', 'credential_missing', 'not_configured'})


def observation_identity(value):
    return json.dumps([value['providerId'], value['upstreamSource'], value['key'], value['symbol']],
                      sort_keys=True, separators=(',', ':'))


def safe_number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return str(value) if value.is_finite() else None
    try:
        return str(value) if math.isfinite(float(value)) else None
    except (TypeError, ValueError, OverflowError):
        return None


def tail_points(rows):
    result = []
    for row in rows[-3:]:
        stamp = row.get('timestamp', row.get('date'))
        if isinstance(stamp, (date, datetime)):
            stamp = stamp.isoformat()
        if not isinstance(stamp, str) or not stamp:
            raise ValueError('观察记录缺少 K 线时间')
        point = {'timestamp': stamp, **{field: safe_number(row.get(field)) for field in FIELDS}}
        # complete 可能来自适配器时钟推算，不能伪装成供应商确认。
        point['adapterComplete'] = row.get('complete') if isinstance(row.get('complete'), bool) else None
        confirmations = {name: row[name] for name in ('sourceConfirmed', 'is_confirmed', 'is_final')
                         if isinstance(row.get(name), bool)}
        point['sourceConfirmation'] = confirmations or None
        point['valueHash'] = hashlib.sha256(json.dumps(
            [point[field] for field in FIELDS], separators=(',', ':')).encode()).hexdigest()
        result.append(point)
    return result


def compare_observations(previous, current):
    matching = [item for item in previous if item.get('status') == 'success'
                and observation_identity(item) == observation_identity(current)]
    if not matching or current.get('status') != 'success':
        return {'result': 'insufficient_observations', 'changedTimestamps': []}
    older = {point['timestamp']: point for point in matching[-1]['points']}
    changed = [point['timestamp'] for point in current['points'] if point['timestamp'] in older
               and point['valueHash'] != older[point['timestamp']]['valueHash']]
    added = [point['timestamp'] for point in current['points'] if point['timestamp'] not in older]
    if changed:
        result = 'same_bar_changed'
    elif added:
        result = 'new_bar_observed'
    else:
        result = 'unchanged_inconclusive'
    return {'result': result, 'changedTimestamps': changed, 'newTimestamps': added}
