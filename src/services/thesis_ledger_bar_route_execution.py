"""精确 K 线策略及目标选择；日线和分钟共用原 V3 路由约束。"""

from typing import Any, Mapping
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataRequest, ProviderExecution, ProviderCallError, ThesisLedgerGatewayError, RouteTarget,
)
from src.services.thesis_ledger_current_data_route import current_data_route_key
from src.services.thesis_ledger_market_v3_adapters import market_v3_bar_adapter_reason
from src.services.thesis_ledger_price_route_access import price_route_admission


def execute_bar_route(
    runtime,
    request: ThesisLedgerDataRequest,
    route_key: Mapping[str, Any],
    *,
    route_target: Mapping[str, Any] | None = None,
) -> ProviderExecution:
    """Fetch one exact V3 bar route without consulting the V1/V2 policy."""
    if not isinstance(request, ThesisLedgerDataRequest):
        raise TypeError("request 必须是 ThesisLedgerDataRequest")
    if (
        not ((request.capability == "DAILY_BAR" and request.timeframe == "1d")
             or (request.capability == "MINUTE_BAR" and request.timeframe in {"1m", "5m", "15m", "30m", "60m"}
                 and request.adjustment == "none" and request.parameters in ({"interactive_chart": True}, {"historical_reconstruction": True})))
        or request.start is None
        or request.end is None
        or request.adjustment not in {"none", "qfq", "hfq"}
        or not isinstance(route_key, Mapping)
        or route_key.get("kind") != "bar"
        or route_key.get("capability") != request.capability
        or route_key.get("timeframe") != request.timeframe
        or route_key.get("adjustment") != request.adjustment
        or route_key.get("assetType") != request.instrument_type
        or route_key.get("market") != current_data_route_key(request.capability, request.instrument_type, request.symbol)["market"]
    ):
        raise ProviderCallError("invalid_request", "Data V3 BarSeries 请求与精确 RouteKey 不匹配")
    pinned_target: dict[str, Any] | None = None
    if route_target is not None:
        if not isinstance(route_target, Mapping) or set(route_target) != {
            "providerId",
            "upstreamSource",
            "routeIndex",
        }:
            raise ProviderCallError("invalid_request", "Data V3 RouteTarget pin 格式非法")
        provider_id = route_target.get("providerId")
        upstream_source = route_target.get("upstreamSource")
        route_index = route_target.get("routeIndex")
        if (
            not isinstance(provider_id, str)
            or not provider_id.strip()
            or provider_id.strip() != provider_id
            or not isinstance(upstream_source, str)
            or not upstream_source.strip()
            or upstream_source.strip() != upstream_source
            or not isinstance(route_index, int)
            or isinstance(route_index, bool)
            or route_index not in {0, 1}
        ):
            raise ProviderCallError("invalid_request", "Data V3 RouteTarget pin 格式非法")
        pinned_target = {
            "providerId": provider_id,
            "upstreamSource": upstream_source,
            "routeIndex": route_index,
        }

    effective_policy = runtime.store.effective_policy_v3()
    if not isinstance(effective_policy, Mapping) or effective_policy.get("contractVersion") != 3:
        raise ProviderCallError("NO_ELIGIBLE_PROVIDER", "Control V3 路由策略不可用")
    if not effective_policy.get("enabled"):
        raise ProviderCallError("NO_ELIGIBLE_PROVIDER", "Control V3 路由策略已禁用")
    if pinned_target is not None:
        effective_revision = effective_policy.get("revision")
        source_desired_revision = effective_policy.get("sourceDesiredRevision")
        if (
            not isinstance(effective_revision, int)
            or isinstance(effective_revision, bool)
            or effective_revision <= 0
            or not isinstance(source_desired_revision, int)
            or isinstance(source_desired_revision, bool)
            or source_desired_revision != effective_revision
        ):
            raise ProviderCallError(
                "invalid_response",
                "Control V3 Effective revision 与 sourceDesiredRevision 不一致",
            )
    routes = effective_policy.get("routes")
    if not isinstance(routes, list):
        raise ProviderCallError("invalid_response", "Control V3 Effective 路由格式非法")
    if pinned_target is not None:
        matching_routes = [
            entry
            for entry in routes
            if isinstance(entry, Mapping) and entry.get("key") == dict(route_key)
        ]
        if len(matching_routes) > 1:
            raise ProviderCallError("invalid_response", "Control V3 Effective 精确行情路由重复")
        route = matching_routes[0] if matching_routes else None
    else:
        route = next(
            (
                entry
                for entry in routes
                if isinstance(entry, Mapping) and entry.get("key") == dict(route_key)
            ),
            None,
        )
    if route is None:
        raise ProviderCallError("NO_ELIGIBLE_PROVIDER", "Control V3 未配置精确行情路由")

    targets = route.get("targets")
    if not isinstance(targets, list):
        raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 格式非法")
    if len(targets) > 2:
        raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 数量非法")
    effective_targets = targets
    if pinned_target is not None:
        target_identities: set[tuple[str, str]] = set()
        for expected_index, candidate in enumerate(targets):
            if not isinstance(candidate, Mapping) or set(candidate) != {
                "providerId",
                "upstreamSource",
                "routeIndex",
                "eligible",
                "reason",
            }:
                raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 格式非法")
            candidate_provider = candidate.get("providerId")
            candidate_source = candidate.get("upstreamSource")
            candidate_index = candidate.get("routeIndex")
            candidate_eligible = candidate.get("eligible")
            candidate_reason = candidate.get("reason")
            if (
                not isinstance(candidate_provider, str)
                or not candidate_provider.strip()
                or candidate_provider.strip() != candidate_provider
                or not isinstance(candidate_source, str)
                or not candidate_source.strip()
                or candidate_source.strip() != candidate_source
                or not isinstance(candidate_index, int)
                or isinstance(candidate_index, bool)
                or candidate_index != expected_index
                or not isinstance(candidate_eligible, bool)
                or (candidate_reason is not None and not isinstance(candidate_reason, str))
                or (
                    candidate_reason is not None
                    and not candidate_reason.strip()
                )
                or candidate_eligible != (candidate_reason is None)
            ):
                raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 状态非法")
            identity = (candidate_provider, candidate_source)
            if identity in target_identities:
                raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 重复")
            target_identities.add(identity)
        matching_targets = [
            target
            for target in targets
            if isinstance(target, Mapping)
            and target.get("providerId") == pinned_target["providerId"]
            and target.get("upstreamSource") == pinned_target["upstreamSource"]
        ]
        if len(matching_targets) != 1:
            raise ProviderCallError(
                "NO_ELIGIBLE_PROVIDER",
                "Data V3 所选 RouteTarget 不在当前 Effective 中或存在歧义",
            )
        target = matching_targets[0]
        route_index = target.get("routeIndex")
        if (
            not isinstance(route_index, int)
            or isinstance(route_index, bool)
            or route_index not in {0, 1}
            or route_index != pinned_target["routeIndex"]
        ):
            raise ProviderCallError(
                "NO_ELIGIBLE_PROVIDER",
                "Data V3 所选 RouteTarget 顺序已变化",
            )
        if target.get("eligible") is not True:
            reason = str(target.get("reason") or "")
            if reason in {"unsupported_adjustment", "basis_incompatible"}:
                raise ProviderCallError("unsupported_adjustment", "所选来源不支持请求的价格口径")
            raise ProviderCallError(
                "NO_ELIGIBLE_PROVIDER",
                "Data V3 所选 RouteTarget 当前不可执行",
            )
        if target.get("reason") is not None:
            raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 状态矛盾")
        effective_targets = [target]

    route_targets: list[RouteTarget] = []
    admission_scope_rejected = False
    seen_target_identities: set[tuple[str, str]] = set()
    for target in effective_targets:
        if not isinstance(target, Mapping):
            raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 格式非法")
        adapter_reason = market_v3_bar_adapter_reason(dict(route_key), dict(target))
        if target.get("eligible") and adapter_reason is not None:
            raise ProviderCallError("invalid_response", "Control V3 标记了未适配的精确行情来源")
        if not target.get("eligible"):
            continue
        provider_id = str(target.get("providerId") or "").strip().lower()
        upstream_source = str(target.get("upstreamSource") or "").strip().lower()
        route_index = target.get("routeIndex")
        if (
            not provider_id
            or not upstream_source
            or not isinstance(route_index, int)
            or isinstance(route_index, bool)
            or route_index not in {0, 1}
        ):
            raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 非法")
        target_identity = (provider_id, upstream_source)
        if target_identity in seen_target_identities:
            raise ProviderCallError("invalid_response", "Control V3 Effective RouteTarget 重复")
        seen_target_identities.add(target_identity)
        exact_target = {"providerId": provider_id, "upstreamSource": upstream_source}
        _, admission_reason = price_route_admission(
            runtime._current_market_v3_admission, request, route_key, exact_target,
        )
        if admission_reason is not None:
            admission_scope_rejected |= admission_reason == "insufficient_coverage"
            continue
        route_targets.append(RouteTarget(provider_id, upstream_source, route_index))
    if not route_targets:
        if admission_scope_rejected:
            raise ProviderCallError(
                "insufficient_coverage",
                "Control V3 RouteTarget admission scope 不覆盖请求窗口",
            )
        reasons = [
            str(target.get("reason") or "")
            for target in targets
            if isinstance(target, Mapping)
        ]
        if any(reason in {"unsupported_adjustment", "basis_incompatible"} for reason in reasons):
            raise ProviderCallError("unsupported_adjustment", "来源不支持请求的价格口径")
        raise ProviderCallError("NO_ELIGIBLE_PROVIDER", "Control V3 没有可执行的精确行情来源")

    def operation(provider_id: str, adapter: Any, upstream_source: str | None) -> Any:
        if request.capability == "MINUTE_BAR":
            from src.services.thesis_ledger_minute_execution import execute_minute
            return execute_minute(runtime, request, route_key, effective_policy, provider_id, upstream_source)
        from src.services.thesis_ledger_exact_bar_execution import execute_exact_bar
        return execute_exact_bar(runtime, request, route_key, effective_policy, provider_id, adapter, upstream_source)

    try:
        return runtime._execute_with_metadata(
            request.capability,
            str(request.instrument_type or "").upper(),
            operation,
            request_id=request.request_id,
            symbol=request.symbol,
            effective_policy_override=effective_policy,
            route_targets_override=route_targets,
        )
    except ThesisLedgerGatewayError:
        raise
    except ProviderCallError as exc:
        raise ThesisLedgerGatewayError(
            exc.code,
            str(exc),
            request,
            retryable=exc.retryable,
            diagnostics=exc.diagnostics,
        ) from exc
