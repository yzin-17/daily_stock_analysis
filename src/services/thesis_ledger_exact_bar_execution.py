"""精确日线调用：凭证快照、接口、配置变更与传输边界。"""

from typing import Mapping
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
from src.services.thesis_ledger_control import ControlContractError
from src.services.thesis_ledger_price_route_access import basic_price_credential_ready, price_route_admission
from src.services.thesis_ledger_market_v3_adapters import basic_market_price_route, market_v3_bar_adapter_reason
from src.services.thesis_ledger_market_v3_facts import market_pagination_contract_v3
from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext


def execute_exact_bar(runtime, request, route_key, effective_policy, provider_id, adapter, upstream_source):
    from src.services.thesis_ledger_provider_runtime import ProviderCallError, DAILY_BAR_TARGET_TIMEOUT_SECONDS

    exact_target = {
        "providerId": provider_id,
        "upstreamSource": str(upstream_source or "").strip().lower(),
    }
    credential_snapshot = None
    if PROVIDER_MANIFESTS.get(provider_id, {}).get("requiresCredential"):
        try:
            credential_snapshot = runtime.store.provider_credential_snapshot(provider_id)
        except (AttributeError, ControlContractError, ValueError):
            raise ProviderCallError(
                "NO_ELIGIBLE_PROVIDER",
                "Control V3 RouteTarget admission 已失效或修订不匹配",
            ) from None
        credential_ready = (basic_price_credential_ready(credential_snapshot)
                            if basic_market_price_route(route_key, exact_target)
                            else _provider_credential_revision_from_snapshot(credential_snapshot) is not None)
        if not credential_ready:
            raise ProviderCallError(
                "NO_ELIGIBLE_PROVIDER",
                "Control V3 RouteTarget admission 已失效或修订不匹配",
            )
    admission, admission_reason = price_route_admission(
        runtime._current_market_v3_admission, request, route_key, exact_target,
        credential_snapshot=credential_snapshot,
    )
    if admission_reason is not None:
        raise ProviderCallError(admission_reason, "来源未就绪或请求超出该能力的审核范围")
    if PROVIDER_MANIFESTS.get(provider_id, {}).get("requiresCredential"):
        verified_adapter = runtime._adapter(
            provider_id,
            snapshot=credential_snapshot,
            cache=CAPABILITY_SOURCES.source(provider_id).cache_verified_daily_factory,
            market_v3_route_key=route_key,
        )
        adapter = verified_adapter
    if market_v3_bar_adapter_reason(route_key, exact_target) is not None:
        raise ProviderCallError("unsupported_adjustment", "来源未证明支持请求的价格口径")
    binding = CAPABILITY_SOURCES.resolve(route_key, exact_target)
    if binding is None:
        raise ProviderCallError("unsupported_source", "来源没有标准精确日线绑定")
    try:
        bound_read = CAPABILITY_SOURCES.daily(request, route_key, exact_target,
                                            ReadContext(adapter, credential_snapshot, DAILY_BAR_TARGET_TIMEOUT_SECONDS))
        source_result = bound_read.frame
    except CapabilityContractError as error:
        raise ProviderCallError(error.code, str(error), retryable=error.retryable, diagnostics=error.diagnostics) from None
    frame = runtime._daily_frame(source_result)
    from src.services.thesis_ledger_configured_read import ConfiguredReadError, check_configured_read_current
    try:
        check_configured_read_current(runtime.store, route_key, exact_target, effective_policy["revision"], credential_snapshot)
    except ConfiguredReadError as error:
        raise ProviderCallError(error.code, str(error)) from None
    attrs = getattr(frame, "attrs", None)
    declared_source = attrs.get("upstream_source") if isinstance(attrs, Mapping) else None
    if declared_source is not None and str(declared_source).strip().lower() != upstream_source:
        raise ProviderCallError("invalid_response", "来源日线响应与 RouteTarget 不一致")
    if request.parameters.get("historical_tradability") is True and getattr(frame, "empty", False) and isinstance(attrs, Mapping) and attrs.get("daily_tradability_input"):
        validated = frame
    else:
        validated = runtime._validate_bars(frame)
    pagination_contract = market_pagination_contract_v3(
        request.instrument_type or "",
        provider_id,
        upstream_source,
    )
    attrs = getattr(validated, "attrs", None)
    if pagination_contract is None or not isinstance(attrs, dict):
        raise ProviderCallError("invalid_response", "来源缺少已知的完整窗口读取契约")
    pages_fetched = bound_read.pages_fetched
    attrs["thesis_ledger_v3_pagination"] = {
        "status": "complete",
        "pagesFetched": pages_fetched,
        "continuationPending": False,
        "requestedStart": request.start,
        "requestedEnd": request.end,
        **pagination_contract,
    }
    return validated
