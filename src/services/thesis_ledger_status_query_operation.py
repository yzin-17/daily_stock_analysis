"""独立状态查询的小型处理器；复用 Runtime 的策略、认证、限时和结果发布边界。"""

from pydantic import ValidationError
from .thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_status_capabilities_v1 import NAMES, REQUESTS
from .thesis_ledger_feature_policy import feature_provider_epoch
from .thesis_ledger_status_acquisition import status_acquisition_cache


def status_query_operation(request, error_type, runtime=None):
    if request.capability not in NAMES:
        return None
    try:
        query = REQUESTS.validate_python(request.parameters or {})
        window = getattr(query, "range", None)
        if (query.routeKey.capability != request.capability or query.symbol != request.symbol or
                query.requestId != request.request_id or query.routeKey.assetType != request.instrument_type or
                request.start != (window.start if window else None) or request.end != (window.end if window else None)):
            raise ValueError("外层请求与本能力参数不一致")
    except (ValidationError, ValueError):
        raise error_type("invalid_request", "独立状态能力请求未通过合同") from None
    binding = CAPABILITY_SOURCES.resolve(query.routeKey.model_dump(), query.routeTarget.model_dump())
    if binding is not None and binding.validate_parameters is not None:
        try:
            binding.validate_parameters(query)
        except CapabilityContractError as error:
            raise error_type(error.code, str(error)) from None
    initial_epoch = None
    if runtime is not None:
        initial_epoch = feature_provider_epoch(runtime.store, [query.routeTarget.providerId])

    def operation(provider, adapter, source):
        target = {"providerId": provider, "upstreamSource": source}
        key = query.routeKey.model_dump()
        binding = CAPABILITY_SOURCES.resolve(key, target)
        if binding is None:
            raise error_type("unsupported_capability", "未登记所选独立状态目标")
        try:
            context = ReadContext(adapter, None)
            if runtime is not None:
                version, enabled = feature_provider_epoch(runtime.store, [provider])[provider]
                if not enabled or initial_epoch != {provider: (version, enabled)}:
                    raise error_type("not_admitted", "来源已关闭或配置代次已变化，不能读取缓存")
                context = ReadContext(adapter, None, acquisition_cache=status_acquisition_cache(runtime),
                                      control_epoch=(query.policyRevision, version))
            result = CAPABILITY_SOURCES.query(binding.contract, query, key, target, context)
            return result.model_dump(exclude_unset=True)
        except CapabilityContractError as error:
            raise error_type(error.code, str(error)) from None
    return operation
