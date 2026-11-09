"""RQData 分钟使用显式账号快照及有界子进程，不复用全局 SDK 登录。"""

import pandas as pd
from data_provider.intraday_projection import project_intraday, cn_identity
from data_provider.rqdata_isolated_process import read_isolated_json, IsolatedReadError, IsolatedStartError, IsolatedTimeoutError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def _read(factory, request):
    from src.services.thesis_ledger_minute_contract_v1 import MinuteRequest
    query = MinuteRequest.model_validate(request)
    code, exchange = cn_identity(query)
    identity = code + ('.XSHG' if exchange == 'SH' else '.XSHE')
    frame = factory().get_price(identity, start_date=query.start, end_date=query.end,
        frequency=query.timeframe, fields=['open', 'high', 'low', 'close', 'volume', 'total_turnover'],
        adjust_type='none', skip_suspended=True, expect_df=True, market='cn')
    if frame is None or frame.empty:
        raise ValueError('empty minute data')
    raw = frame.reset_index()
    if 'order_book_id' not in raw or set(raw['order_book_id']) != {identity}:
        raise ValueError('minute security mismatch')
    if len(raw) >= 10000:
        raise ValueError('minute response limit')
    return project_intraday(raw, query, 'rqdata', columns={'datetime': 'timestamp', 'total_turnover': 'amount'},
        convention='source-native', volume_unit='share', amount_unit='CNY').model_dump(mode='json')


def read_minute(query, context):
    from src.services.thesis_ledger_minute_contract_v1 import MinuteResult
    try:
        result = read_isolated_json(context.adapter, _read, (query.model_dump(mode='json'),),
            timeout_seconds=min(context.timeout_seconds, 4.5), temp_prefix='rqdata-minute-')
        return MinuteResult.model_validate(result)
    except IsolatedTimeoutError:
        raise CapabilityContractError('timeout', 'RQData 分钟读取超时') from None
    except (IsolatedReadError, IsolatedStartError, ValueError):
        raise CapabilityContractError('invalid_response', 'RQData 分钟读取或字段验证失败') from None
