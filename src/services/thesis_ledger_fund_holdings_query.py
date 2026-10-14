"""持仓标准查询处理器；来源执行不被参数异常捕获覆盖。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_fund_holdings_contract_v1 import CONTRACT, HoldingsRequest


def read_holdings(request, provider, adapter, upstream, error_type):
    try:
        query = HoldingsRequest.from_request(request)
    except ValueError as error:
        raise error_type('invalid_request', '持仓请求未通过合同') from error
    try:
        return CAPABILITY_SOURCES.query(CONTRACT, query, query.route_key(),
            {'providerId': provider, 'upstreamSource': upstream}, ReadContext(adapter))
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None
