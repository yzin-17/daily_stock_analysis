"""Finnhub 原生不复权分钟；固定单证券、纽约窗口及正常交易时段。"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
import pandas as pd
from data_provider.intraday_projection import project_intraday
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def read_minute(query, context):
    from data_provider.multimarket_exact_daily import _http_json
    zone = ZoneInfo('America/New_York')
    first = datetime.combine(date.fromisoformat(query.start), time(), zone)
    last = datetime.combine(date.fromisoformat(query.end) + timedelta(days=1), time(), zone)
    # 交互图保留来源可返回的子窗口，不将单月结果声明为完整历史覆盖。
    first = max(first, last - timedelta(days=28))
    payload = _http_json('https://finnhub.io/api/v1/stock/candle', {
        'symbol': query.symbol.removesuffix('.US'), 'resolution': query.timeframe[:-1],
        'from': int(first.timestamp()), 'to': int(last.timestamp()) - 1, 'token': context.adapter._api_key,
    })
    if payload.get('s') != 'ok':
        raise CapabilityContractError('not_covered', 'Finnhub 分钟行情不可用，请检查权限与覆盖范围')
    try:
        raw = pd.DataFrame({name: payload[key] for name, key in
            {'timestamp': 't', 'open': 'o', 'high': 'h', 'low': 'l', 'close': 'c', 'volume': 'v'}.items()})
        clocks = pd.to_datetime(raw['timestamp'], unit='s', utc=True).dt.tz_convert(zone)
        # 使用已锁定的本地交易所日历，包含假日与提前收市；不增加远程日历请求。
        import exchange_calendars as xcals
        calendar = xcals.get_calendar('XNYS')
        sessions = calendar.sessions_in_range(query.start, query.end)
        keep = pd.Series(False, index=raw.index)
        for session in sessions:
            keep |= (clocks >= calendar.session_open(session)) & (clocks < calendar.session_close(session))
        raw = raw[keep]
    except (KeyError, ValueError, TypeError):
        raise CapabilityContractError('invalid_response', 'Finnhub 分钟数组或时间无效') from None
    return project_intraday(raw, query, 'finnhub', time_unit='s', convention='interval-start', volume_unit='share')
