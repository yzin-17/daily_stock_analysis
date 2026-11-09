"""腾讯官方网页使用的沪深分钟 K 线，保留原生时间和缺失成交额。"""

import pandas as pd
import requests
from data_provider.intraday_projection import cn_identity, project_intraday
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def project_payload(payload, query):
    code, exchange = cn_identity(query)
    identity = exchange.lower() + code
    try:
        if payload['code'] != 0:
            raise ValueError('upstream status')
        data = payload['data'][identity]
        if data['qt'][identity][2] != code:
            raise ValueError('security mismatch')
        values = data['m' + query.timeframe[:-1]]
        if not isinstance(values, list) or any(not isinstance(row, list) or len(row) < 6 for row in values):
            raise ValueError('minute rows')
        raw = pd.DataFrame([{'timestamp': pd.to_datetime(row[0], format='%Y%m%d%H%M', errors='raise'),
            'open': row[1], 'close': row[2], 'high': row[3], 'low': row[4], 'volume': row[5]} for row in values])
    except (KeyError, TypeError, ValueError, IndexError):
        raise CapabilityContractError('invalid_response', '腾讯分钟证券、周期或字段无效') from None
    return project_intraday(raw, query, 'tencent', convention='source-native', volume_unit='hand')


def read_minute(query, context):
    if query.timeframe not in {'5m', '15m', '30m', '60m'}:
        raise CapabilityContractError('not_adapted', '腾讯分钟 K 线仅接入 5/15/30/60 分钟')
    code, exchange = cn_identity(query)
    response = requests.get('https://ifzq.gtimg.cn/appstock/app/kline/mkline',
        params={'param': exchange.lower() + code + ',m' + query.timeframe[:-1] + ',,320'},
        headers={'Referer': 'https://gu.qq.com/'}, timeout=context.timeout_seconds, allow_redirects=False)
    response.raise_for_status()
    return project_payload(response.json(), query)
