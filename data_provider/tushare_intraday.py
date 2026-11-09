"""Tushare 独立分钟权限对应的沪深股票／ETF 原生查询。"""

from data_provider.intraday_projection import cn_identity, project_intraday
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def read_minute(query, context):
    from data_provider.tushare_history_request import TushareHistoryRequest
    cn_identity(query)
    budget = TushareHistoryRequest(context.adapter, context.timeout_seconds)
    budget.before_call()
    raw = budget.client.query('etf_mins' if query.instrument_type == 'ETF' else 'stk_mins',
        ts_code=query.symbol, freq=query.timeframe[:-1] + 'min',
        start_date=query.start + ' 00:00:00', end_date=query.end + ' 23:59:59')
    budget.check_complete()
    if raw is not None and not raw.empty:
        if 'ts_code' not in raw or not raw['ts_code'].eq(query.symbol).all():
            raise CapabilityContractError('invalid_response', 'Tushare 分钟证券身份不一致')
        if len(raw) >= 8000:
            raise CapabilityContractError('insufficient_coverage', 'Tushare 分钟窗口达到单次上限')
    return project_intraday(raw, query, 'tushare', columns={'trade_time': 'timestamp', 'vol': 'volume'},
                            convention='interval-end', volume_unit='share', amount_unit='CNY')
