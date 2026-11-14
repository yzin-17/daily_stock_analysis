"""原报价适配器保留明确的上游限制，避免吞成空报价或重复请求。"""

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_provider_errors import classify_provider_failure


def raise_source_restriction(error):
    code = classify_provider_failure(error)
    if code in {'rate_limited', 'rate_limited_global', 'quota_unavailable', 'permission_denied', 'authentication_failed'}:
        raise CapabilityContractError(code, '上游来源读取受限') from None
