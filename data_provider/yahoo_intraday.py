"""Yahoo 原生不复权分钟，固定单标的且不调用日线备用来源。"""

from datetime import date, timedelta
from data_provider.intraday_projection import project_intraday
from src.services.thesis_ledger_minute_contract_v1 import minute_market


def read_minute(query, context):
    import yfinance as yf
    symbol = query.symbol
    market = minute_market(symbol)
    if market == 'CN':
        symbol = symbol.removesuffix('.SH') + '.SS' if symbol.endswith('.SH') else symbol
    elif market == 'HK':
        symbol = symbol.removesuffix('.HK').lstrip('0').zfill(4) + '.HK'
    else:
        symbol = symbol.removesuffix('.US')
    ticker = yf.Ticker(symbol)
    raw = ticker.history(start=query.start, end=(date.fromisoformat(query.end) + timedelta(days=1)).isoformat(),
        interval=query.timeframe, auto_adjust=False, back_adjust=False, actions=False,
        prepost=False, repair=False, raise_errors=True, timeout=context.timeout_seconds)
    raw = raw.reset_index()
    return project_intraday(raw, query, 'yfinance',
        columns={'Datetime': 'timestamp', 'Open': 'open', 'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'},
        convention='interval-start', volume_unit='share')
