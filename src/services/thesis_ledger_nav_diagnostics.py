"""普通 NAV 诊断共享标准合同，不读取 SDK 字段或选择业务备用。"""

from .thesis_ledger_capability_contracts import CapabilityContractError
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_fund_nav_contract_v1 import CONTRACT as LATEST, LatestNavRequest
from .thesis_ledger_fund_nav_history_contract_v1 import CONTRACT as HISTORY, NavHistoryRequest


def diagnostic_target(provider, capability, error_type):
    targets = [target for key, target, _ in CAPABILITY_SOURCES.routes(capability)
               if key == {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': capability}
               and target['providerId'] == provider]
    if len(targets) != 1:
        raise error_type('unsupported_source', 'NAV 诊断需要唯一精确来源绑定')
    return targets[0]


def read_nav_diagnostic(key, target, symbol, context, error_type, *, start=None, today=None):
    if key['capability'] == 'FUND_NAV':
        contract, request = LATEST, LatestNavRequest(symbol=symbol)
    elif key['capability'] == 'FUND_NAV_HISTORY':
        contract, request = HISTORY, NavHistoryRequest(symbol=symbol, start=start, end=today)
    else:
        raise error_type('unsupported_capability', '不是 NAV 诊断能力')
    try:
        result = CAPABILITY_SOURCES.query(contract, request, key, target, context)
    except CapabilityContractError as error:
        raise error_type(error.code, str(error), retryable=error.retryable) from None
    if today is not None:
        if result.source_reported_end is None or result.source_reported_end > today:
            raise error_type('invalid_response', 'NAV 检查缺少完整来源日期或包含未来估值日')
        if start is not None and result.source_reported_end < start:
            raise error_type('insufficient_coverage', 'NAV 检查窗口没有数据')
    return result
