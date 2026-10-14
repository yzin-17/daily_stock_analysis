"""持仓诊断消费标准绑定，不读取 SDK 字段或保存业务政策。"""

from .thesis_ledger_capability_contracts import CapabilityContractError
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_fund_holdings_contract_v1 import CONTRACT, HoldingsRequest


def diagnostic_target(provider, error_type):
    key = HoldingsRequest(symbol='000001.OF').route_key()
    targets = [target for route, target, _ in CAPABILITY_SOURCES.routes('FUND_HOLDINGS')
               if route == key and target['providerId'] == provider]
    if len(targets) != 1:
        raise error_type('unsupported_source', '持仓诊断需要唯一精确来源绑定')
    return targets[0]


def read_holdings_diagnostic(key, target, symbol, context, error_type):
    request = HoldingsRequest(symbol=symbol)
    if key != request.route_key():
        raise error_type('unsupported_capability', '不是持仓诊断精确能力')
    try:
        return CAPABILITY_SOURCES.query(CONTRACT, request, key, target, context)
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None
