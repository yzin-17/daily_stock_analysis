"""Alpha Vantage 原生分钟；收费权限失败不得退回日线或调整价格。"""

import pandas as pd
from data_provider.intraday_projection import project_intraday
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def read_minute(query, context):
    from data_provider.multimarket_exact_daily import _http_json
    symbol = query.symbol.removesuffix('.US')
    payload = _http_json('https://www.alphavantage.co/query', {
        'function': 'TIME_SERIES_INTRADAY', 'symbol': symbol, 'interval': query.timeframe[:-1] + 'min',
        'adjusted': 'false', 'extended_hours': 'false', 'outputsize': 'full',
        'apikey': context.adapter._api_key,
    })
    if 'Note' in payload or 'Information' in payload:
        from src.services.thesis_ledger_provider_errors import classify_provider_failure
        raise CapabilityContractError(classify_provider_failure(payload.get('Note') or payload.get('Information')),
                                      'Alpha Vantage 分钟权限或额度不可用')
    metadata = payload.get('Meta Data', {})
    if metadata.get('2. Symbol') != symbol or metadata.get('4. Interval') != query.timeframe[:-1] + 'min':
        raise CapabilityContractError('invalid_response', 'Alpha Vantage 分钟证券或周期不一致')
    values = payload.get('Time Series (' + query.timeframe[:-1] + 'min)')
    if not isinstance(values, dict):
        raise CapabilityContractError('not_covered', 'Alpha Vantage 未返回目标分钟行情')
    raw = pd.DataFrame([{'timestamp': stamp, **{
        field: row.get(key) for field, key in {'open': '1. open', 'high': '2. high',
        'low': '3. low', 'close': '4. close', 'volume': '5. volume'}.items()}}
        for stamp, row in values.items()])
    return project_intraday(raw, query, 'alphavantage', convention='interval-start', volume_unit='share')
