"""TickFlow 原生分钟，保留未说明的时间边界及量额单位。"""

import pandas as pd
from data_provider.intraday_projection import cn_identity, project_intraday
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def read_minute(query, context):
    cn_identity(query)
    adapter = context.adapter
    client = adapter._get_client()
    if client is None:
        raise CapabilityContractError('credential_missing', 'TickFlow 凭证未配置')
    payload = client.klines.get(query.symbol, period=query.timeframe, count=10000,
        start_time=adapter._date_to_ms(query.start), end_time=adapter._date_to_ms(query.end, end_of_day=True),
        adjust='none', as_dataframe=False)
    try:
        raw = pd.DataFrame(payload)
    except (ValueError, TypeError):
        raise CapabilityContractError('invalid_response', 'TickFlow 分钟列式数据无效') from None
    if len(raw) >= 10000:
        raise CapabilityContractError('insufficient_coverage', 'TickFlow 分钟窗口达到单次上限')
    return project_intraday(raw, query, 'tickflow', convention='source-native', time_unit='ms')
