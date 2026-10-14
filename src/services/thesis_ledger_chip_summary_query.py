"""筹码能力查询与诊断；标准参数校验不覆盖来源错误分类。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_chip_summary_contract_v1 import CONTRACT, ChipRequest


def read_chip(request, provider, adapter, upstream, error_type):
    try:
        query = ChipRequest.from_request(request)
    except ValueError as error:
        raise error_type('invalid_request', '筹码请求未通过合同') from error
    return read_chip_query(query, query.route_key(), {'providerId': provider, 'upstreamSource': upstream},
                           ReadContext(adapter), error_type)


def read_chip_query(query, key, target, context, error_type):
    try:
        return CAPABILITY_SOURCES.query(CONTRACT, query, key, target, context)
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None


def diagnostic_target(provider, error_type):
    key = ChipRequest(symbol='600519.SH').route_key()
    targets = [target for route, target, _ in CAPABILITY_SOURCES.routes('CHIP_SUMMARY')
               if route == key and target['providerId'] == provider]
    if len(targets) != 1:
        raise error_type('unsupported_source', '筹码诊断需要唯一精确来源绑定')
    return targets[0]
