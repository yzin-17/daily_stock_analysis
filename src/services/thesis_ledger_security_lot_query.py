"""当前每手参考的标准处理器桥接，主备仍由现行执行器决定。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_security_lot_contract_v1 import CAPABILITY, CONTRACT, LotRequest


def read_lot(request, key, target, context, error_type):
    if CAPABILITY_SOURCES.resolve(key, target) is None:
        raise error_type("unsupported_source", "每手参考未匹配精确来源")
    try:
        return CAPABILITY_SOURCES.query(CONTRACT, request, key, target, context).model_dump()
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None


def lot_query_operation(request, error_type):
    if request.capability != CAPABILITY:
        return None
    try:
        query = LotRequest.from_request(request)
    except ValueError:
        raise error_type("invalid_request", "每手仅接受港股股票当前参考，不接受历史或额外参数") from None

    def operation(provider, adapter, source):
        return read_lot(query, query.route_key(), {"providerId": provider, "upstreamSource": source},
                        ReadContext(adapter), error_type)
    return operation
