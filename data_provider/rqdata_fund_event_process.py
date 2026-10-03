"""RQData 基金事件的进程隔离读取；客户端工厂只在子进程初始化账号。"""

from .rqdata_fund_event_contract import validate_fund_event_scope
from .rqdata_isolated_process import (
    IsolatedReadError, IsolatedStartError, IsolatedTimeoutError, read_isolated_json,
)


def _read_event(factory, kind, symbol, options):
    from .rqdata_fund_split_reader import fetch_rqdata_fund_splits
    from .rqdata_fund_dividend_reader import fetch_rqdata_fund_dividends
    reader = fetch_rqdata_fund_splits if kind == 'split' else fetch_rqdata_fund_dividends
    return reader(factory(), symbol, **options, before_call=lambda: None, after_call=lambda: None)


def read_rqdata_fund_event_isolated(factory, kind, symbol, *, timeout_seconds, **options):
    """工厂必须可被 spawn 序列化；初始化、读取及标准化共同受总期限约束。"""
    validate_fund_event_scope(symbol, options.get('query_fund_code'), options.get('instrument_type'),
                              options.get('start'), options.get('end'))
    allowed = {'query_fund_code', 'instrument_type', 'start', 'end', 'maximum_rows'}
    if kind == 'dividend':
        allowed.add('currency')
        if options.get('currency') not in {'CNY', 'HKD', 'USD'}:
            raise ValueError('rqdata requires verified currency')
    if kind not in {'split', 'dividend'} or set(options) - allowed or not callable(factory):
        raise ValueError('rqdata invalid isolated request')
    rows = options.get('maximum_rows', 2000)
    if type(rows) is not int or not 1 <= rows <= 10000:
        raise ValueError('rqdata invalid response budget')
    try:
        return read_isolated_json(
            factory, _read_event, (kind, symbol, options),
            timeout_seconds=timeout_seconds, temp_prefix='rqdata-fund-event-',
        )
    except IsolatedStartError:
        raise RuntimeError('rqdata_process_start_failed') from None
    except IsolatedTimeoutError:
        raise TimeoutError('rqdata_read_timeout') from None
    except IsolatedReadError:
        raise RuntimeError('rqdata_read_failed') from None
