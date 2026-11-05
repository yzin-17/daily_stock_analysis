"""市场资料的代码作用域与路由身份，不包含来源协议。"""

import re
from .thesis_ledger_reference_catalog_v1 import CATALOG
from .thesis_ledger_reference_public_catalog_v1 import SYMBOL_MARKETS
from .thesis_ledger_reference_derivative_catalog_v1 import DERIVATIVE_ASSETS, OPTION_UNDERLYINGS

SYMBOL_SCOPES = ('security', 'etf', 'future', 'option')


def asset_type(dataset):
    return DERIVATIVE_ASSETS.get(dataset, 'ETF' if CATALOG[dataset][1] == 'etf' else 'STOCK')


def valid_symbol(dataset, symbol):
    scope = CATALOG[dataset][1]
    patterns = {
        'security': r'(?:(?:60\d{4}|688\d{3})\.SH|(?:00\d{4}|30\d{4})\.SZ)',
        'etf': r'(?:5[168]\d{4}\.SH|15\d{4}\.SZ)',
        'future': r'[A-Z]{1,2}(?:0|\d{2}(?:0[1-9]|1[0-2]))',
        'option': r'\d{8}',
    }
    if scope not in patterns:
        return symbol is None
    if not isinstance(symbol, str) or re.fullmatch(patterns[scope], symbol) is None:
        return False
    if dataset == 'etf-option-contracts' and symbol not in OPTION_UNDERLYINGS:
        return False
    market = SYMBOL_MARKETS.get(dataset)
    return market is None or symbol.endswith('.' + market)


def runtime_symbol(query):
    return query.symbol or 'CN.' + asset_type(query.dataset)
