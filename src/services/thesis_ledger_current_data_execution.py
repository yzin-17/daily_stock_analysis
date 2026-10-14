"""当前普通数据读取的精确策略、凭证和返回范围检查。"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

from src.services.thesis_ledger_current_data_route import (
    CurrentDataRouteError, current_data_route_key, select_current_data_targets,
)
from src.services.thesis_ledger_local_rules import LOCAL_RULE_CAPABILITIES
from src.services.thesis_ledger_status_capabilities_v1 import NAMES as STATUS_QUERY_CAPABILITIES
from src.services.thesis_ledger_feature_policy import feature_provider_epoch
from src.services.thesis_ledger_price_route_access import configured_data_route
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_provider_runtime import (
    PROVIDER_MANIFESTS, ProviderCallError, ProviderExecution, RouteTarget,
    ThesisLedgerDataRequest, ThesisLedgerGatewayError, _log_quote_event,
)


def execute_current_data_request(
    runtime,
    request: ThesisLedgerDataRequest,
    capability: str,
    instrument_type: str,
    operation: Callable[[str, Any], Any],
    *,
    budget_symbol: str | None = None,
    request_id: str | None = None,
) -> ProviderExecution:
    """Execute the current data route and attach request identity."""
    try:
        effective = runtime.store.effective_policy_v3()
        targets = select_current_data_targets(
            effective, capability=capability, instrument_type=instrument_type,
            symbol=request.symbol,
        )
        route_key = current_data_route_key(capability, instrument_type, request.symbol)
        if capability in LOCAL_RULE_CAPABILITIES or capability in STATUS_QUERY_CAPABILITIES:
            parameters = request.parameters or {}
            pin = parameters.get("routeTarget")
            if not isinstance(pin, Mapping) or parameters.get("routeKey") != route_key:
                raise CurrentDataRouteError("invalid_request", "历史规则请求身份或目标缺失")
            targets = [item for item in targets if item == (
                pin.get("providerId"), pin.get("upstreamSource"), pin.get("routeIndex"),
            )]
            if not targets:
                raise CurrentDataRouteError("NO_ELIGIBLE_PROVIDER", "历史规则目标不在当前配置中")
            if capability in STATUS_QUERY_CAPABILITIES and parameters.get("policyRevision") != effective.get("revision"):
                raise CurrentDataRouteError("not_admitted", "独立功能请求的政策代次已失效")
        market_timezone = {
            "CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York",
        }[route_key["market"]]
        today = datetime.now(ZoneInfo(market_timezone)).date().isoformat()
        date_from = request.start or today
        date_to = request.end or today
        credential_snapshots = {}
        scoped_targets = []
        for provider_id, source, route_index in targets:
            target = {"providerId": provider_id, "upstreamSource": source}
            if PROVIDER_MANIFESTS[provider_id].get("requiresCredential"):
                credential_snapshots[provider_id] = runtime.store.provider_credential_snapshot(provider_id)
            if configured_data_route(route_key, target):
                scoped_targets.append(RouteTarget(provider_id, source, route_index))
                continue
            admission = runtime._current_market_v3_admission(
                route_key, target,
            )
            if admission and admission.get("admissionState") == "admitted" and route_admission_scope_applies(
                admission, symbol=request.symbol, date_from=date_from, date_to=date_to,
            ):
                scoped_targets.append(RouteTarget(provider_id, source, route_index))
        if not scoped_targets:
            raise CurrentDataRouteError("not_admitted", "当前来源准入未覆盖请求标的或日期")
        provider_epoch = None
        if capability in STATUS_QUERY_CAPABILITIES:
            provider_epoch = feature_provider_epoch(runtime.store, [item.provider_id for item in scoped_targets])
        def checked_operation(provider_id, adapter, source):
            value = operation(provider_id, adapter, source)
            if capability in {"FUND_NAV", "FUND_NAV_HISTORY"}:
                try:
                    if capability == "FUND_NAV":
                        from src.services.thesis_ledger_fund_nav_query import bound_latest_nav_date
                        value = bound_latest_nav_date(value, today, CurrentDataRouteError)
                    else:
                        from src.services.thesis_ledger_fund_nav_history_query import bound_nav_history_dates
                        value = bound_nav_history_dates(value, request.start, request.end, today, CurrentDataRouteError)
                except CurrentDataRouteError as error:
                    raise ProviderCallError(error.code, str(error)) from None
            return value

        result = runtime._execute_with_metadata(
            capability,
            instrument_type,
            checked_operation,
            budget_symbol=budget_symbol,
            request_id=request_id or request.request_id,
            symbol=request.symbol,
            effective_policy_override=effective,
            route_targets_override=scoped_targets,
        )
        current = runtime.store.effective_policy_v3()
        if provider_epoch is not None and feature_provider_epoch(runtime.store, [result.provider]) != {
            result.provider: provider_epoch[result.provider]
        }:
            raise CurrentDataRouteError("not_admitted", "功能读取期间来源控制版本已变更")
        if not isinstance(current, Mapping) or current.get("revision") != effective.get("revision"):
            raise CurrentDataRouteError("not_admitted", "数据返回前当前策略已变更")
        try:
            current_targets = select_current_data_targets(
                current, capability=capability, instrument_type=instrument_type,
                symbol=request.symbol,
            )
        except CurrentDataRouteError as exc:
            raise CurrentDataRouteError("not_admitted", "数据返回前目标已失效") from exc
        if (result.provider, result.upstream_source) not in {
            (provider_id, source) for provider_id, source, _index in current_targets
        }:
            raise CurrentDataRouteError("not_admitted", "数据返回前目标已失效")
        if not configured_data_route(route_key, {
            "providerId": result.provider, "upstreamSource": result.upstream_source,
        }):
            admission = runtime._current_market_v3_admission(
                route_key, {"providerId": result.provider,
                            "upstreamSource": result.upstream_source},
            )
            if not admission or not route_admission_scope_applies(
                admission, symbol=request.symbol, date_from=date_from, date_to=date_to,
            ):
                raise CurrentDataRouteError("not_admitted", "数据返回前准入已失效")
        snapshot = credential_snapshots.get(result.provider)
        if snapshot is not None and runtime.store.provider_credential_snapshot(result.provider) != snapshot:
            raise CurrentDataRouteError("credential_missing", "数据返回前来源凭证已变更")
        return result
    except ThesisLedgerGatewayError:
        raise
    except CurrentDataRouteError as exc:
        if capability.upper() == "REALTIME_QUOTE":
            _log_quote_event(
                stage="provider-selection", request_id=request_id or request.request_id,
                symbol=request.symbol, instrument_type=instrument_type,
                status="rejected", duration_ms=0.0,
                error_code=exc.code, providers=[],
            )
        raise ThesisLedgerGatewayError(exc.code, str(exc), request) from exc
    except ProviderCallError as exc:
        raise ThesisLedgerGatewayError(
            exc.code,
            str(exc),
            request,
            retryable=exc.retryable,
            diagnostics=exc.diagnostics,
        ) from exc
