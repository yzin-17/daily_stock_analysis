"""公共费用小型能力处理器；沿原单目标路由输出标准JSON。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_public_fees_contract_v1 import CAPABILITY, CONTRACT, FeeRequest


def fees_query_operation(request, error_type):
    if request.capability != CAPABILITY:
        return None
    try:
        query = FeeRequest.from_request(request)
    except (ValueError, TypeError):
        raise error_type('invalid_request', '公共费用请求未通过标准合同') from None

    def operation(provider, adapter, source):
        try:
            result = CAPABILITY_SOURCES.query(CONTRACT, query, query.routeKey.model_dump(),
                {'providerId':provider, 'upstreamSource':source}, ReadContext(adapter))
            return result.model_dump(mode='json', exclude_unset=True)
        except CapabilityContractError as error:
            raise error_type(error.code, str(error), retryable=error.retryable, diagnostics=error.diagnostics) from None
    return operation
