"""旧交互调用接缝：来源读取只在适配层执行，服务投影现行错误。"""


def read_interactive_hithink_daily(request, snapshot):
    from data_provider.hithink_interactive_daily import read_interactive_hithink_daily as read
    from .thesis_ledger_capability_contracts import CapabilityContractError
    from .thesis_ledger_provider_runtime import ProviderCallError
    try:
        return read(request, snapshot)
    except CapabilityContractError as error:
        raise ProviderCallError(error.code, str(error), retryable=error.retryable, diagnostics=error.diagnostics) from None
