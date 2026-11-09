"""BaoStock 原生 5/15/30/60 分钟股票，不声明 1 分钟和未确认 ETF。"""

import pandas as pd
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from data_provider.intraday_projection import project_intraday


def read_minute(query, context):
    from data_provider.intraday_projection import cn_identity
    code, exchange = cn_identity(query)
    identity = exchange.lower() + '.' + code
    with context.adapter._baostock_session() as sdk:
        response = sdk.query_history_k_data_plus(code=identity,
            fields='date,time,code,open,high,low,close,volume,amount', start_date=query.start,
            end_date=query.end, frequency=query.timeframe[:-1], adjustflag='3')
        if response.error_code != '0':
            raise CapabilityContractError('upstream_failure', 'BaoStock 分钟查询失败')
        rows = []
        while response.next():
            rows.append(response.get_row_data())
        raw = pd.DataFrame(rows, columns=response.fields)
    if not raw.empty:
        if not raw['code'].eq(identity).all():
            raise CapabilityContractError('invalid_response', 'BaoStock 分钟证券身份不一致')
        raw['timestamp'] = pd.to_datetime(raw['time'], format='%Y%m%d%H%M%S%f', errors='raise')
    return project_intraday(raw, query, 'baostock', convention='interval-end', volume_unit='share', amount_unit='CNY')
