"""研报按需单页请求；固定单目标并隔离来源、政策与绑定修订变化。"""

from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_current_data_route import select_current_data_targets, CurrentDataRouteError
from .thesis_ledger_feature_policy import feature_provider_epoch
from .thesis_ledger_report_metadata_contract_v2 import CAPABILITY, CONTRACT, ReportMetadataRequest


def read_report_metadata_probe(runtime, key, target, snapshot, symbol, _end):
    from uuid import uuid4
    from .thesis_ledger_provider_runtime import ProviderCallError
    query = ReportMetadataRequest(symbol=symbol, requestId=str(uuid4()), page=1, pageSize=2, requestBudgetMs=3000)
    adapter = runtime._adapter(target['providerId'], snapshot=snapshot, cache=False, probe=True)
    try:
        result = CAPABILITY_SOURCES.query(CONTRACT, query, key, target,
            ReadContext(adapter, snapshot, timeout_seconds=3, probe=True))
    except CapabilityContractError as error:
        raise ProviderCallError(error.code, str(error), retryable=False) from None
    if not result.items:
        raise ProviderCallError('not_covered', '来源检查页没有可核验的研报元数据')
    return {'symbol': symbol, 'start': query.start, 'end': query.end}


def report_metadata_operation(request, error_type, runtime):
    if request.capability != CAPABILITY:
        return None
    try:
        query = ReportMetadataRequest.from_request(request)
    except ValueError:
        raise error_type('invalid_request', '研报元数据请求的身份、日期筛选或分页预算无效') from None
    effective = runtime.store.effective_policy_v3()
    revision = effective.get('revision')
    try:
        targets = select_current_data_targets(effective, capability=CAPABILITY,
                                             instrument_type='STOCK', symbol=query.symbol)
    except CurrentDataRouteError as error:
        raise error_type(error.code, str(error)) from None
    if len(targets) != 1:
        raise error_type('not_admitted', '研报单页读取需要一个明确有效目标，不执行来源切换')
    provider, source, _index = targets[0]
    epochs = feature_provider_epoch(runtime.store, [provider])
    key = query.route_key()
    target = {'providerId': provider, 'upstreamSource': source}
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if (not epochs[provider][1] or binding is None or binding.contract is not CONTRACT
            or binding.admission_required is not False):
        raise error_type('not_admitted', '研报来源未启用或当前绑定不可用')
    revisions = (binding.adapter_revision, binding.source_revision)

    def operation(actual_provider, adapter, actual_source):
        if (actual_provider, actual_source) != (provider, source):
            raise error_type('not_admitted', '研报执行目标已发生变化')
        def current():
            current_binding = CAPABILITY_SOURCES.resolve(key, target)
            return (runtime.store.effective_policy_v3().get('revision') == revision
                    and feature_provider_epoch(runtime.store, [provider]) == epochs
                    and current_binding is not None and current_binding.contract is CONTRACT
                    and current_binding.admission_required is False
                    and (current_binding.adapter_revision, current_binding.source_revision) == revisions)

        if not current():
            raise error_type('not_admitted', '研报执行前控制状态已变化')
        try:
            result = CAPABILITY_SOURCES.query(CONTRACT, query, key, target,
                ReadContext(adapter, timeout_seconds=query.requestBudgetMs / 1000,
                            database_path=runtime.store.database_path))
        except CapabilityContractError as error:
            raise error_type(error.code, str(error), retryable=False) from None
        if not current():
            raise error_type('not_admitted', '研报来源返回时控制或绑定修订已变化，结果不予采纳')
        if (result.adapterRevision, result.sourceRevision) != revisions:
            raise error_type('invalid_response', '研报来源结果修订与当前绑定不符')
        return result.model_dump(mode='json')

    return operation
