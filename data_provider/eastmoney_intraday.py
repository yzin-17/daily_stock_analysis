"""东方财富原生分钟；两种 SDK 各自固定读取，不互相降级。"""

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from data_provider.intraday_projection import project_intraday, cn_identity

COLUMNS = {'时间': 'timestamp', '日期': 'timestamp', '开盘': 'open', '收盘': 'close',
           '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount'}


def akshare_minute(query, context):
    import akshare as ak
    code, _ = cn_identity(query)
    method = ak.fund_etf_hist_min_em if query.instrument_type == 'ETF' else ak.stock_zh_a_hist_min_em
    from data_provider.akshare_fetcher import _akshare_call_with_timeout
    raw = _akshare_call_with_timeout(method, timeout=context.timeout_seconds, symbol=code, period=query.timeframe[:-1], adjust='',
                 start_date=query.start + ' 00:00:00', end_date=query.end + ' 23:59:59')
    return project_intraday(raw, query, 'eastmoney', columns=COLUMNS, convention='interval-end',
                            volume_unit='hand', amount_unit='CNY')


def efinance_minute(query, context):
    import efinance as ef
    from data_provider.efinance_fetcher import _ef_call_with_timeout
    code, exchange = cn_identity(query)
    raw = _ef_call_with_timeout(ef.stock.get_quote_history,
        stock_codes=('1' if exchange == 'SH' else '0') + '.' + code,
        beg=query.start.replace('-', ''), end=query.end.replace('-', ''), klt=int(query.timeframe[:-1]),
        fqt=0, quote_id_mode=True, use_id_cache=False, timeout=context.timeout_seconds)
    if not raw.empty and ('股票代码' not in raw or not raw['股票代码'].astype(str).str.zfill(6).eq(code).all()):
        raise CapabilityContractError('invalid_response', 'efinance 分钟证券身份不一致')
    return project_intraday(raw, query, 'eastmoney', columns=COLUMNS, convention='interval-end',
                            volume_unit='hand', amount_unit='CNY')
