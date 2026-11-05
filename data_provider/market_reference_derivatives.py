"""衍生品资料的固定协议分派，不含备用来源或隐式扫描。"""

from .market_reference_derivatives_sources import SOURCES
from .market_reference_futures_daily import read_futures_daily
from .market_reference_futures_rank import read_futures_rank
from .market_reference_futures_sina import read_futures_sina
from .market_reference_etf_options import read_etf_options
from .market_reference_convertible import read_convertible


def reader(dataset):
    if dataset.startswith('futures-daily-'):
        return read_futures_daily
    if dataset.startswith('futures-rank-'):
        return read_futures_rank
    if dataset.startswith('etf-option-'):
        return read_etf_options
    if dataset == 'convertible-bond-reference':
        return read_convertible
    return read_futures_sina


DERIVATIVE_READERS = {name: (*source, reader(name)) for name, source in SOURCES.items()}
