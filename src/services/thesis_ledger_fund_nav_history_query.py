"""普通净值历史的标准处理器与原报告日边界。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_fund_nav_history_contract_v1 import CONTRACT, NavHistoryRequest


def read_nav_history(request, provider, adapter, upstream, error_type):
    try:
        query = NavHistoryRequest.from_request(request)
    except ValueError as error:
        raise error_type('invalid_request', '净值历史请求未通过合同') from error
    try:
        return CAPABILITY_SOURCES.query(CONTRACT, query, query.route_key(),
            {'providerId': provider, 'upstreamSource': upstream}, ReadContext(adapter))
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None


def bound_nav_history_dates(result, start, end, today, error_type):
    if any(point.reported_date > today for point in result.points):
        raise error_type('invalid_response', '基金净值来源日期无效')
    points = tuple(point for point in result.points if point.reported_date <= (end or today)
                   and (start is None or point.reported_date >= start))
    if not points:
        raise error_type('insufficient_coverage', '请求窗口没有基金净值数据')
    return result.model_copy(update={'points': points})
