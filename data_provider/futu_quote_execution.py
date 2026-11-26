"""保留富途当前快照的执行资料；未知字段不影响普通报价，也不补造准入。"""

from hashlib import sha256
import json
from numbers import Integral


def _scalar(value):
    return value.item() if hasattr(value, 'item') else value


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else None


def current_execution(code, asset_type, static, snapshot, market_time, observed_at):
    market = code.split('.', 1)[0]
    if market not in {'HK', 'US'}:
        return None
    suspended = _scalar(snapshot.get('suspension'))
    delisted = _scalar(static.get('delisting'))
    status = _text(snapshot.get('sec_status'))
    state = 'unknown'
    if suspended is True or status in {'SUSPENDED', 'RECOVERABLE_CIRCUIT_BREAKER', 'UN_RECOVERABLE_CIRCUIT_BREAKER'}:
        state = 'suspended'
    elif suspended is False and status == 'NORMAL' and delisted is False:
        state = 'normal'
    lot, security_id = _scalar(snapshot.get('lot_size')), _scalar(static.get('stock_id'))
    result = {
        'version': 1, 'symbol': code[3:] + '.' + market, 'market': market, 'assetType': asset_type,
        'exchange': _text(static.get('exchange_type')),
        'securityId': str(security_id) if isinstance(security_id, Integral) and not isinstance(security_id, bool) and security_id > 0 else None,
        'sharesPerLot': str(lot) if isinstance(lot, Integral) and not isinstance(lot, bool) and lot > 0 else None,
        'delisted': delisted if isinstance(delisted, bool) else None,
        'tradingState': state, 'securityStatus': status, 'marketTime': market_time,
        'observedAt': observed_at, 'provider': 'futu',
    }
    # 摘要不包含假定价差：price_spread 是相邻报价差，不能用作法定最小价差。
    result['responseSha256'] = sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result
