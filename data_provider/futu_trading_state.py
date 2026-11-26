"""深市 ETF 当前停牌观察；保留快照字段，不借用价格或申赎状态。"""

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from zoneinfo import ZoneInfo

from .futu_market import FutuMarketError, _unique_row
from .futu_quote_execution import _scalar, _text

SOURCE = 'https://openapi.futunn.com/futu-api-doc/quote/get-market-snapshot.html'
CN = ZoneInfo('Asia/Shanghai')


def read_futu_trading_state(sdk, host, port, symbol, *, clock=lambda: datetime.now(timezone.utc)):
    if not isinstance(symbol, str) or not re.fullmatch(r'\d{6}\.SZ', symbol):
        raise FutuMarketError('invalid_request', '当前状态需要规范深市 ETF 代码')
    code = 'SZ.' + symbol[:6]
    context = sdk.OpenQuoteContext(host=host, port=port)
    try:
        row = _unique_row(*context.get_market_snapshot([code]), sdk, code)
        observed = clock()
        if observed.utcoffset() is None:
            raise FutuMarketError('invalid_response', '快照观察时刻必须包含时区')
        day = observed.astimezone(CN).date().isoformat()
        suspended = _scalar(row.get('suspension'))
        status = _text(row.get('sec_status'))
        state = 'unknown'
        if suspended is True or status in {'SUSPENDED', 'RECOVERABLE_CIRCUIT_BREAKER', 'UN_RECOVERABLE_CIRCUIT_BREAKER'}:
            state = 'suspended'
        elif suspended is False and status == 'NORMAL':
            state = 'normal'
        # update_time 是价格更新时间；当日停牌的价格可较旧，不要求其秒级刷新。
        market_time = None
        try:
            raw_time = row.get('update_time')
            fmt = '%Y-%m-%d %H:%M:%S.%f' if '.' in str(raw_time) else '%Y-%m-%d %H:%M:%S'
            source = datetime.strptime(raw_time, fmt).replace(tzinfo=CN)
            if source > observed:
                raise ValueError('future snapshot')
            market_time = source.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            state = 'unknown'
        fields = {'code': code, 'suspension': suspended if isinstance(suspended, bool) else None,
                  'securityStatus': status, 'marketTime': market_time}
        evidence = {'provider': 'futu', 'source': SOURCE, 'fields': fields,
                    'responseSha256': sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()}
        return {'symbol': symbol, 'assetType': 'ETF', 'day': day, 'observedAt': observed.isoformat(),
                'state': state, 'evidence': evidence}
    finally:
        context.close()
