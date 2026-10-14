"""最新净值在现行 Runtime 中的标准能力处理器。"""

from pydantic import ValidationError
from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_fund_nav_contract_v1 import CONTRACT, LatestNavRequest


def read_latest_nav(request, provider, adapter, upstream, error_type):
    try:
        query = LatestNavRequest.from_request(request)
    except (ValueError, ValidationError) as error:
        raise error_type('invalid_request', '最新净值请求未通过合同') from error
    try:
        return CAPABILITY_SOURCES.query(CONTRACT, query, query.route_key(),
            {'providerId': provider, 'upstreamSource': upstream}, ReadContext(adapter))
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None


def bound_latest_nav_date(result, today, error_type):
    if result.point.reported_date > today:
        raise error_type('invalid_response', '基金净值来源日期无效')
    return result
