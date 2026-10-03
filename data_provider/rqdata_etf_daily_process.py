"""RQData ETF 日线 SDK 的隔离进程读取，不授予行情准入。"""

from .rqdata_etf_daily_contract import (
    normalize_rqdata_etf_daily, rqdata_etf_daily_request,
)
from .rqdata_isolated_process import (
    IsolatedReadError, IsolatedStartError, IsolatedTimeoutError, read_isolated_json,
)


def _read_etf_daily(factory, symbol, order_book_id, start, end, maximum_rows):
    request = rqdata_etf_daily_request(
        symbol, order_book_id, start, end, maximum_rows=maximum_rows,
    )
    frame = factory().get_price(**request)
    return normalize_rqdata_etf_daily(
        frame, symbol, order_book_id, start, end, maximum_rows=maximum_rows,
    )


def read_rqdata_etf_daily_isolated(
    factory, symbol, order_book_id, start, end, *, timeout_seconds, maximum_rows=366,
):
    """只执行精确来源调用；当前身份/账号/策略修订须由生产调用方核验。"""
    rqdata_etf_daily_request(
        symbol, order_book_id, start, end, maximum_rows=maximum_rows,
    )
    try:
        return read_isolated_json(
            factory, _read_etf_daily,
            (symbol, order_book_id, start, end, maximum_rows),
            timeout_seconds=timeout_seconds, temp_prefix='rqdata-etf-daily-',
        )
    except IsolatedStartError:
        raise RuntimeError('rqdata_price_process_start_failed') from None
    except IsolatedTimeoutError:
        raise TimeoutError('rqdata_price_read_timeout') from None
    except IsolatedReadError:
        raise RuntimeError('rqdata_price_read_failed') from None
