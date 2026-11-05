"""路由合同的资产身份；资料能力与普通行情序列分别准入。"""

INSTRUMENT_TYPES = ('STOCK', 'ETF', 'MUTUAL_FUND', 'LOF', 'INDEX', 'BOND', 'CONVERTIBLE_BOND')
DATA_INSTRUMENT_TYPES = (*INSTRUMENT_TYPES, 'FUTURE', 'OPTION')


def route_asset_type_allowed(kind, asset_type):
    return asset_type in (DATA_INSTRUMENT_TYPES if kind == 'data' else INSTRUMENT_TYPES)
