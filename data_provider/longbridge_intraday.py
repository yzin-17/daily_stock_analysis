"""长桥原生分钟；显式正常时段和不复权，响应上限不冒充完整窗口。"""

from datetime import date
import pandas as pd
from data_provider.intraday_projection import project_intraday
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def read_minute(query, context):
    from longbridge.openapi import Period, AdjustType, TradeSessions
    client = context.adapter._get_ctx()
    if client is None:
        raise CapabilityContractError('credential_missing', '长桥凭证或连接未就绪')
    candles = client.history_candlesticks_by_date(
        query.symbol, getattr(Period, 'Min_' + query.timeframe[:-1]), AdjustType.NoAdjust,
        date.fromisoformat(query.start), date.fromisoformat(query.end), TradeSessions.Intraday)
    if len(candles) >= 1000:
        raise CapabilityContractError('invalid_response', '长桥分钟响应达到上限，请缩小日期窗口')
    raw = pd.DataFrame([{'timestamp': candle.timestamp,
        **{field: getattr(candle, field, None) for field in ('open', 'high', 'low', 'close', 'volume')},
        'amount': getattr(candle, 'turnover', None)} for candle in candles])
    return project_intraday(raw, query, 'longbridge', convention='source-native', volume_unit='share')
