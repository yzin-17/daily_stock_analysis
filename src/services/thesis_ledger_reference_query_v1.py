"""市场资料按需读取；固定目标并拒绝控制状态变化后的结果。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_current_data_route import select_current_data_targets, CurrentDataRouteError
from .thesis_ledger_feature_policy import feature_provider_epoch
from .thesis_ledger_reference_contract_v1 import CONTRACTS, ReferenceRequest
from .thesis_ledger_reference_scope_v1 import runtime_symbol


def reference_operation(request, error_type, runtime):
    contract = CONTRACTS.get(request.capability)
    if contract is None:
        return None
    try:
        query = ReferenceRequest.from_request(request)
    except ValueError:
        raise error_type('invalid_request', '市场资料身份、日期或分页请求无效') from None
    key = query.route_key()
    revision = runtime.store.effective_policy_v3().get('revision')
    try:
        targets = select_current_data_targets(runtime.store.effective_policy_v3(), capability=request.capability,
                                             instrument_type=key['assetType'], symbol=runtime_symbol(query))
    except CurrentDataRouteError as error:
        raise error_type(error.code, str(error)) from None
    if len(targets) != 1:
        raise error_type('not_admitted', '市场资料需要一个明确有效来源')
    provider, source, _ = targets[0]
    epochs = feature_provider_epoch(runtime.store, [provider])
    target = {'providerId': provider, 'upstreamSource': source}
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if (not epochs[provider][1] or binding is None or binding.contract is not contract or binding.admission_required is not False):
        raise error_type('not_admitted', '市场资料来源未启用或绑定不可用')
    revisions = (binding.adapter_revision, binding.source_revision)

    def operation(actual_provider, adapter, actual_source):
        if (actual_provider, actual_source) != (provider, source):
            raise error_type('not_admitted', '市场资料执行来源已变化')

        def current():
            actual = CAPABILITY_SOURCES.resolve(key, target)
            return (runtime.store.effective_policy_v3().get('revision') == revision
                    and feature_provider_epoch(runtime.store, [provider]) == epochs
                    and actual is not None and actual.contract is contract and actual.admission_required is False
                    and (actual.adapter_revision, actual.source_revision) == revisions)

        if not current():
            raise error_type('not_admitted', '市场资料执行前控制状态已变化')
        try:
            value = CAPABILITY_SOURCES.query(contract, query, key, target,
                ReadContext(adapter, timeout_seconds=query.requestBudgetMs / 1000, database_path=runtime.store.database_path))
        except CapabilityContractError as error:
            raise error_type(error.code, str(error), retryable=False) from None
        if not current():
            raise error_type('not_admitted', '市场资料返回时控制状态已变化')
        if (value.adapterRevision, value.sourceRevision) != revisions:
            raise error_type('invalid_response', '市场资料响应修订与实际绑定不符')
        return value.model_dump(mode='json')

    return operation
