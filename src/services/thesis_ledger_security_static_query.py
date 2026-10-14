"""当前资料处理器的现行执行器桥接；来源实现由精确绑定决定。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_security_static_contract_v1 import CAPABILITY, CONTRACT, StaticRequest


def read_static(request, key, target, context, error_type):
    if CAPABILITY_SOURCES.resolve(key, target) is None:
        raise error_type("unsupported_source", "当前资料未匹配精确来源")
    try:
        return CAPABILITY_SOURCES.query(CONTRACT, request, key, target, context).model_dump()
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None


def static_query_operation(request, error_type):
    if request.capability != CAPABILITY:
        return None
    try:
        query = StaticRequest.from_request(request)
    except ValueError:
        raise error_type("invalid_request", "当前资料需要规范证券及类型，不接受历史或额外参数") from None

    def operation(provider, adapter, source):
        return read_static(query, query.route_key(), {"providerId": provider, "upstreamSource": source},
                           ReadContext(adapter), error_type)
    return operation
