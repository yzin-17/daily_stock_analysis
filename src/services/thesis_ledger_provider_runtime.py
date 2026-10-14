# -*- coding: utf-8 -*-
"""DSA ThesisLedger consumer namespace 的 Provider 路由执行器。

它只按 DSA SQLite 中的 Effective Policy 顺序执行。旧的
``DataFetcherManager`` 仍服务 DSA native analysis；本模块不把 native 的默认
优先级或隐藏 fallback 泄漏到 ThesisLedger consumer。
"""

from __future__ import annotations

import importlib
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from src.services.thesis_ledger_factory_execution import execute_source_factory
from typing import Any, Callable, Mapping

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_oauth_contract import OAuthStateError
from src.services.thesis_ledger_nav_dates import validate_nav_history
from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS,
    ControlContractError,
    ThesisLedgerControlStore,
    _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_market_v3_adapters import HITHINK_STOCK_HISTORY_SOURCE, HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1, market_v3_bar_adapter_reason, basic_market_price_route
from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
    market_calendar_evidence_v3,
    market_listing_fact_v3,
    market_pagination_contract_v3,
)
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_price_route_access import (
    basic_price_credential_ready, basic_price_route_configured, price_route_admission, configured_data_route,
)
from src.services.thesis_ledger_market_v3_pagination import MarketPaginationError, market_pagination_proof_v3
from src.services.thesis_ledger_catalog_manifest_v3 import catalog_manifest_matches_v3
from src.services.thesis_ledger_event_v3_adapters import event_admission_reader
from src.services.thesis_ledger_current_data_route import CurrentDataRouteError, current_data_route_key, select_current_data_targets
from src.services.thesis_ledger_quote_dispatch import realtime_quote as dispatch_realtime_quote
from src.services.thesis_ledger_local_rules import (
    LOCAL_RULE_CAPABILITIES, LOCAL_RULE_ADAPTER_IMPORTS,
    local_rule_source_ready,
)

from src.services.thesis_ledger_reference_data import reference_data_operation
from src.services import thesis_ledger_market_v3_revisions as _route_revisions
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import QuoteAdmissionContext, QuoteGuardContext, ReadContext, provider_symbol_for_contract

# Preserve the existing import surface while revision ownership moves out of the executor.
MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION = _route_revisions.MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION
market_v3_current_route_revisions = _route_revisions.market_v3_current_route_revisions
_market_v3_admission_matches_current = _route_revisions._market_v3_admission_matches_current

logger = logging.getLogger(__name__)

_PROVIDER_ADAPTER_IMPORTS = {
    **{source.provider_id: source.adapter_import for source in CAPABILITY_SOURCES.sources if source.adapter_import},
    **LOCAL_RULE_ADAPTER_IMPORTS,
}

DAILY_BAR_TARGET_TIMEOUT_SECONDS = 4.5


def _log_quote_event(
    *,
    stage: str,
    request_id: str | None,
    symbol: str,
    instrument_type: str,
    status: str,
    duration_ms: int = 0,
    provider: str | None = None,
    provider_symbol: str | None = None,
    error_code: str | None = None,
    providers: list[str] | None = None,
    attempt: int | None = None,
) -> None:
    """Emit bounded, request-correlated Quote evidence without raw payloads."""
    event: dict[str, Any] = {
        "event": "thesis_ledger.quote",
        "stage": stage,
        "requestId": request_id or "unknown",
        "traceId": request_id or "unknown",
        "symbol": symbol,
        "instrumentType": instrument_type,
        "status": status,
        "durationMs": max(0, int(duration_ms)),
    }
    if provider is not None:
        event["provider"] = provider
    if provider_symbol is not None:
        event["providerSymbol"] = provider_symbol
    if error_code is not None:
        event["errorCode"] = error_code
    if providers is not None:
        event["providers"] = providers
    if attempt is not None:
        event["attempt"] = attempt
    logger.info(json.dumps(event, ensure_ascii=False, separators=(",", ":")))


def classify_provider_exception(error: Exception) -> str:
    """Map adapter failures to stable categories without returning raw details."""

    status_code = getattr(error, "status_code", None)
    if status_code is not None:
        status_code = int(status_code)
        if status_code == 429:
            return "rate_limited"
        if status_code == 401:
            return "authentication_failed"
        if status_code == 403:
            return "permission_denied"
        if status_code >= 500:
            return "upstream_failure"
    error_kind = str(getattr(error, "error_kind", "")).lower()
    if error_kind in {"rate_limited", "authentication_failed", "permission_denied"}:
        return error_kind
    if error_kind == "timeout":
        return "network_failure"

    text = str(error).lower()
    if "429" in text or "rate limit" in text or "too many request" in text or "quota" in text:
        return "rate_limited"
    if "401" in text or "unauthor" in text or "invalid api key" in text or "invalid token" in text:
        return "authentication_failed"
    if "403" in text or "forbidden" in text or "permission" in text or "access denied" in text:
        return "permission_denied"
    if isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return "network_failure"
    if any(term in text for term in ("timeout", "timed out", "connection reset", "dns")):
        return "network_failure"
    return "upstream_failure"


class ProviderCallError(Exception):
    """Stable, provider-safe error raised inside the ThesisLedger namespace."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
        diagnostic_id: str | None = None,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.request_id = request_id
        self.diagnostic_id = diagnostic_id or request_id
        self.diagnostics = dict(diagnostics or {})

    def detail(self, *, contract_version: int = 3) -> dict[str, Any]:
        """Return a stable error projection without exposing upstream details."""
        request_id = self.request_id or str(uuid.uuid4())
        detail = {
            "contractVersion": contract_version,
            "code": self.code,
            "message": str(self),
            "requestId": request_id,
            "diagnosticId": self.diagnostic_id or request_id,
        }
        if self.diagnostics:
            detail["diagnostics"] = self.diagnostics
        return detail


@dataclass(frozen=True)
class ThesisLedgerDataRequest:
    """ThesisLedger 各数据能力共用的现行请求合同。"""

    capability: str
    symbol: str
    timeframe: str | None = None
    start: str | None = None
    end: str | None = None
    limit: int | None = None
    instrument_type: str | None = None
    adjustment: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def __post_init__(self) -> None:
        capability = str(self.capability or "").strip().upper()
        symbol = str(self.symbol or "").strip().upper()
        if not capability:
            raise ProviderCallError("invalid_request", "缺少 Capability")
        if not symbol:
            raise ProviderCallError("invalid_request", "缺少标的 symbol")
        if self.limit is not None and (
            not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit <= 0
        ):
            raise ProviderCallError("invalid_request", "limit 必须是正整数")
        if not isinstance(self.parameters, Mapping):
            raise ProviderCallError("invalid_request", "parameters 必须是对象")
        object.__setattr__(self, "capability", capability)
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timeframe", self.timeframe.strip().lower() if self.timeframe else None)
        object.__setattr__(self, "instrument_type", self.instrument_type.upper() if self.instrument_type else None)
        normalized_adjustment = self.adjustment.strip().lower() if self.adjustment else None
        if normalized_adjustment is not None and normalized_adjustment not in {"none", "qfq", "hfq"}:
            raise ProviderCallError("invalid_request", "adjustment 必须是 none、qfq 或 hfq")
        object.__setattr__(self, "adjustment", normalized_adjustment)
        object.__setattr__(self, "parameters", dict(self.parameters))
        object.__setattr__(
            self,
            "request_id",
            str(self.request_id or uuid.uuid4()).strip() or str(uuid.uuid4()),
        )


@dataclass(frozen=True)
class RouteTarget:
    """One explicit adapter/source pair from the Effective Policy."""

    provider_id: str
    upstream_source: str | None
    route_index: int | None = None

    @property
    def key(self) -> str:
        return f"{self.provider_id}:{self.upstream_source or '-'}"


@dataclass(frozen=True)
class ProviderExecution:
    """Normalized runtime result plus policy and provenance metadata."""

    value: Any
    capability: str
    instrument_type: str
    provider: str
    fallback_used: bool
    effective_policy: Mapping[str, Any] | None
    route: tuple[str, ...]
    attempted_providers: tuple[str, ...]
    upstream_source: str | None = None
    route_index: int = 0
    provider_revision: str = "unknown"
    route_targets: tuple[RouteTarget, ...] = ()
    attempted_targets: tuple[RouteTarget, ...] = ()

    @property
    def effective_revision(self) -> int | None:
        """Return the DSA Effective Policy revision used for this request."""
        if not self.effective_policy:
            return None
        revision = self.effective_policy.get("revision")
        return int(revision) if isinstance(revision, int) else None

    @property
    def source_desired_revision(self) -> int | None:
        """Return the Desired Policy revision projected into Effective Policy."""
        if not self.effective_policy:
            return None
        revision = self.effective_policy.get("sourceDesiredRevision")
        return int(revision) if isinstance(revision, int) else None

    @property
    def provenance(self) -> dict[str, Any]:
        """Return source-pinned metadata without exposing provider payloads."""
        return {
            "providerId": self.provider,
            "upstreamSource": self.upstream_source,
            "routeIndex": self.route_index,
            "effectivePolicyRevision": self.effective_revision,
            "providerRevision": self.provider_revision,
            "route": [
                {"providerId": target.provider_id, "upstreamSource": target.upstream_source}
                for target in self.route_targets
            ],
        }


class ThesisLedgerGatewayError(ProviderCallError):
    """Stable error carrying the request identity at the gateway boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        request: ThesisLedgerDataRequest,
        *,
        retryable: bool = False,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(
            code,
            message,
            retryable=retryable,
            request_id=request.request_id,
            diagnostic_id=request.request_id,
            diagnostics=diagnostics,
        )
        self.request = request

    def detail(self, *, contract_version: int = 3) -> dict[str, Any]:
        """Return a request-correlated stable Contract error projection."""
        return super().detail(contract_version=contract_version)


def validate_fund_nav_history_rows(rows: list[tuple[Any, Any]]) -> None:
    """校验基金净值历史的非空、日期唯一升序和正数净值约束。"""
    if not rows:
        raise ProviderCallError("not_covered", "Provider 未返回基金净值历史")
    try:
        validate_nav_history(rows)
    except (TypeError, ValueError) as exc:
        raise ProviderCallError("invalid_response", str(exc)) from exc


class NoEligibleProviderError(ProviderCallError):
    """Effective Policy 当前没有可执行的 Provider 路由。"""

    def __init__(self, capability: str, instrument_type: str) -> None:
        """Build a stable error for an empty eligible-provider route."""
        super().__init__(
            "NO_ELIGIBLE_PROVIDER",
            f"没有可执行的 {capability}/{instrument_type} Provider",
        )


def instrument_type_for_symbol(symbol: str) -> str:
    """Infer the Contract instrument type from a canonical or bare symbol."""
    normalized = symbol.strip().upper()
    if normalized.endswith(".OF"):
        return "MUTUAL_FUND"
    code = normalized.split(".", 1)[0]
    if code.startswith(("15", "16", "18", "51", "52", "56", "58")):
        return "ETF"
    return "STOCK"


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: float = 0.0
    half_open: bool = False


class _ScopedCircuit:
    """Maintain circuit state independently for each consumer route key."""

    def __init__(self) -> None:
        """Initialize the in-memory circuit state table."""
        self._states: dict[str, _CircuitState] = {}
        self._lock = threading.RLock()

    def allow(self, key: str, now: float) -> bool:
        """Return whether the route may execute at the supplied monotonic time."""
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            if state.open_until <= now:
                if state.open_until > 0:
                    if state.half_open:
                        return False
                    state.half_open = True
                return True
            return False

    def success(self, key: str) -> None:
        """Reset a route after a successful Provider call."""
        with self._lock:
            self._states[key] = _CircuitState()

    def hydrate_open(self, key: str, now: float, remaining_seconds: float) -> None:
        """Restore an externally persisted open circuit with remaining TTL."""
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.failures = max(state.failures, 3)
            state.open_until = max(state.open_until, now + max(0.0, remaining_seconds))
            state.half_open = False

    def failure(self, key: str, now: float) -> tuple[str, int]:
        """Record a failure and return the new circuit state and count."""
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.failures += 1
            if state.failures >= 3:
                state.open_until = now + 60
                state.half_open = False
                return "open", state.failures
            return "closed", state.failures

    def state(self, key: str, now: float) -> str:
        """Return the route's closed, open, or half-open state."""
        with self._lock:
            state = self._states.get(key)
            if state is None or state.open_until <= now:
                return "half-open" if state and state.open_until > 0 else "closed"
            return "open"


class ThesisLedgerProviderRuntime:
    """Execute one complete record/sequence against the configured route."""

    def __init__(
        self,
        store: ThesisLedgerControlStore | None = None,
        *,
        adapters: dict[str, Any] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.store = store or ThesisLedgerControlStore()
        self.adapters = adapters or {}
        self._adapter_versions: dict[str, ProviderCredentialSnapshot] = {}
        self._adapter_cache_lock = threading.RLock()
        self.clock = clock or time.monotonic
        self.circuit = _ScopedCircuit()

    def _current_market_v3_admission(
        self,
        key: Mapping[str, Any],
        target: Mapping[str, Any],
        *,
        credential_snapshot: ProviderCredentialSnapshot | None = None,
        provider_manifest: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any] | None:
        """Read fresh admission evidence and compare it with local revisions."""
        get_admission = getattr(self.store, "get_route_admission_v3", None)
        provider_id = str(target.get("providerId") or "").strip().lower()
        provider_manifest = provider_manifest if provider_manifest is not None else PROVIDER_MANIFESTS.get(provider_id)
        if not callable(get_admission) or provider_manifest is None:
            return None
        if reader := event_admission_reader(key, target):
            return reader(self.store, key, target, provider_manifest)
        reader = getattr(CAPABILITY_SOURCES.source(provider_id), 'quote_admission_reader', None)
        if (reader is not None and key.get('kind') == 'data' and key.get('capability') == 'REALTIME_QUOTE'
                and CAPABILITY_SOURCES.resolve(key, target) is not None):
            return reader(QuoteAdmissionContext(self.store, key, target, provider_manifest, credential_snapshot))
        credential_revision = None
        if getattr(CAPABILITY_SOURCES.source(provider_id), 'credential_revision_kind', None) == 'hmac':
            if credential_snapshot is None:
                try:
                    credential_snapshot = self.store.provider_credential_snapshot(provider_id)
                except (AttributeError, ControlContractError, ValueError):
                    return None
            credential_revision = _provider_credential_revision_from_snapshot(
                credential_snapshot
            )
            if credential_revision is None:
                return None
        admission = get_admission(key=dict(key), target=dict(target))
        if not isinstance(admission, Mapping) or not _market_v3_admission_matches_current(
            admission,
            key,
            target,
            provider_manifest,
            credential_version=provider_manifest.get("credentialVersion"),
            credential_revision=credential_revision,
        ):
            return None
        return admission

    def _catalog_market_v3_admission_is_current(
        self,
        key: Mapping[str, Any],
        target: Mapping[str, Any],
        provider: Mapping[str, Any],
    ) -> bool:
        ready = local_rule_source_ready(key, target)
        if ready is not None:
            return ready
        if basic_market_price_route(key, target) or configured_data_route(key, target):
            return basic_price_route_configured(self.store, key, target)
        return self._current_market_v3_admission(key, target, provider_manifest=provider) is not None

    def _adapter(
        self,
        provider_id: str,
        snapshot: ProviderCredentialSnapshot | None = None,
        *,
        cache: bool = True,
        probe: bool = False,
        market_v3_route_key: Mapping[str, Any] | None = None,
    ) -> Any:
        source = CAPABILITY_SOURCES.source(provider_id)
        factory = CAPABILITY_SOURCES.factory_for_route(provider_id, market_v3_route_key)
        if factory is not None and source.factory_scope == "route":
            return self.adapters.get(provider_id) or execute_source_factory(factory, ProviderCallError, ReadContext(
                None, snapshot, timeout_seconds=5 if probe else DAILY_BAR_TARGET_TIMEOUT_SECONDS, probe=probe,
                database_path=getattr(self.store, "database_path", None),
            ))
        if source is not None and market_v3_route_key is None and source.unscoped_factory is not None:
            return self.adapters.get(provider_id) or execute_source_factory(source.unscoped_factory, ProviderCallError, ReadContext(None, snapshot, probe=probe))
        with self._adapter_cache_lock:
            if provider_id in self.adapters and provider_id not in self._adapter_versions:
                return self.adapters[provider_id]
        try:
            snapshot = snapshot or self.store.provider_credential_snapshot(provider_id)
            version = snapshot
            if source is not None and source.require_credential_revision_for_cache:
                revision = _provider_credential_revision_from_snapshot(snapshot)
                if revision is None:
                    cache = False
            with self._adapter_cache_lock:
                if (
                    cache
                    and provider_id in self.adapters
                    and self._adapter_versions.get(provider_id) == version
                ):
                    return self.adapters[provider_id]
            adapter_import = _PROVIDER_ADAPTER_IMPORTS.get(provider_id)
            if adapter_import is None and source is None:
                raise ProviderCallError("UNKNOWN_PROVIDER", "未知 Provider")
            adapter_class = None
            if source is None:
                module_name, class_name = adapter_import
                adapter_class = getattr(importlib.import_module(module_name), class_name)
            values = snapshot.values
            manifest = PROVIDER_MANIFESTS.get(provider_id, {})
            if manifest.get("requiresCredential") and not values and not (
                source is not None and source.allow_empty_environment_credentials and snapshot.source == "environment"
            ):
                raise ProviderCallError("not_configured", "Provider 凭证未配置")
            if source is not None:
                adapter = execute_source_factory(source.factory, ProviderCallError, ReadContext(
                    None, snapshot, timeout_seconds=5 if probe else DAILY_BAR_TARGET_TIMEOUT_SECONDS, probe=probe,
                    database_path=getattr(self.store, "database_path", None),
                ))
            else:
                adapter = adapter_class()
        except ProviderCallError:
            raise
        except OAuthStateError as exc:
            raise ProviderCallError(exc.code, "Longbridge OAuth 凭证不可用") from exc
        except ControlContractError as exc:
            raise ProviderCallError(exc.code, "Provider 页面凭证无法安全解析") from exc
        except Exception as exc:  # optional adapter dependency/configuration.
            raise ProviderCallError("not_configured", "Provider 适配器未就绪") from exc
        if cache:
            with self._adapter_cache_lock:
                # Publish adapter and version together. In-flight callers may
                # still hold the old adapter, so it must be released naturally.
                if (
                    provider_id in self.adapters
                    and self._adapter_versions.get(provider_id) == version
                ):
                    return self.adapters[provider_id]
                self.adapters[provider_id] = adapter
                self._adapter_versions[provider_id] = version
        return adapter

    @staticmethod
    def _daily_frame(adapter_result: Any) -> Any:
        """兼容 BaseFetcher 的 DataFrame 与 manager 的 ``(frame, source)`` 返回值。"""
        from .thesis_ledger_daily_probe_validation import daily_frame
        from .thesis_ledger_capability_contracts import CapabilityContractError
        try:
            return daily_frame(adapter_result)
        except CapabilityContractError as error:
            raise ProviderCallError(error.code, str(error)) from None

    @staticmethod
    def _realtime_quote(
        adapter: Any,
        provider_id: str,
        symbol: str,
        instrument_type: str | None = None,
        upstream_source: str | None = None,
    ) -> Any:
        """在 ETF 边界选择已确认的单标适配器，未知实现保持 fail-closed。"""
        return dispatch_realtime_quote(
            adapter, provider_id, symbol, instrument_type, upstream_source,
            ProviderCallError,
        )

    @staticmethod
    def _finite_number(value: Any, field: str, *, positive: bool = False) -> float:
        try:
            number = float(value) if not isinstance(value, bool) else math.nan
        except (TypeError, ValueError) as exc:
            raise ProviderCallError(
                "invalid_response", f"Provider 响应字段 {field} 非法"
            ) from exc
        if not math.isfinite(number) or number < 0 or (positive and number <= 0):
            raise ProviderCallError(
                "invalid_response", f"Provider 响应字段 {field} 非法"
            )
        return number

    @classmethod
    def _validate_quote(cls, value: Any) -> Any:
        from src.services.thesis_ledger_quote_validation import validate_quote
        from src.services.thesis_ledger_capability_contracts import CapabilityContractError
        try:
            return validate_quote(value)
        except CapabilityContractError as error:
            raise ProviderCallError(error.code, str(error)) from None

    @classmethod
    def _validate_bars(cls, frame: Any) -> Any:
        from .thesis_ledger_daily_probe_validation import validate_bars
        from .thesis_ledger_capability_contracts import CapabilityContractError
        try:
            return validate_bars(frame)
        except CapabilityContractError as error:
            raise ProviderCallError(error.code, str(error)) from None



    def _execute_with_metadata(
        self,
        capability: str,
        instrument_type: str,
        operation: Callable[[str, Any, str | None], Any],
        *,
        budget_symbol: str | None = None,
        request_id: str | None = None,
        symbol: str | None = None,
        effective_policy_override: Mapping[str, Any],
        route_targets_override: list[RouteTarget],
    ) -> ProviderExecution:
        quote_request = capability.upper() == "REALTIME_QUOTE"
        quote_started = time.monotonic()
        logged_symbol = symbol or budget_symbol or "unknown"
        effective_policy = effective_policy_override
        if effective_policy.get("contractVersion") != 3:
            raise ProviderCallError("unsupported_policy", "只接受当前 Control Policy")
        route_targets = list(route_targets_override)
        providers = [target.provider_id for target in route_targets]
        if not providers:
            if quote_request:
                _log_quote_event(
                    stage="provider-selection",
                    request_id=request_id,
                    symbol=logged_symbol,
                    instrument_type=instrument_type,
                    status="rejected",
                    duration_ms=(time.monotonic() - quote_started) * 1000,
                    error_code="NO_ELIGIBLE_PROVIDER",
                    providers=[],
                )
            raise NoEligibleProviderError(capability, instrument_type)
        fallback_used = False
        attempted_providers: list[str] = []
        attempted_targets: list[RouteTarget] = []
        last_error: ProviderCallError | None = None
        budgeted_quote = (
            capability.upper() == "REALTIME_QUOTE"
            and instrument_type.upper() == "ETF"
            and bool(budget_symbol)
        )
        for index, target in enumerate(route_targets):
            provider_id = target.provider_id
            upstream_source = target.upstream_source
            route_index = target.route_index if target.route_index is not None else index
            if route_index > 0:
                fallback_used = True
            key = f"thesis-ledger:{provider_id}:{capability}:{instrument_type}:{upstream_source}"
            now = self.clock()
            persisted_health = self.store.health(
                provider_id,
                capability,
                instrument_type,
                upstream_source=upstream_source,
            )
            if persisted_health and persisted_health.get("circuit") == "open":
                try:
                    checked_at = datetime.fromisoformat(
                        str(persisted_health["checked_at"]).replace("Z", "+00:00")
                    )
                    elapsed = (datetime.now(timezone.utc) - checked_at).total_seconds()
                    if elapsed < 60:
                        self.circuit.hydrate_open(key, now, 60 - max(0.0, elapsed))
                except (KeyError, TypeError, ValueError):
                    pass
            if not self.circuit.allow(key, now):
                last_error = ProviderCallError("circuit_open", "Provider 熔断已打开")
                if quote_request:
                    _log_quote_event(
                        stage="provider-qualification",
                        request_id=request_id,
                        symbol=logged_symbol,
                        provider_symbol=budget_symbol,
                        instrument_type=instrument_type,
                        status="rejected",
                        duration_ms=(time.monotonic() - quote_started) * 1000,
                        provider=provider_id,
                        error_code=last_error.code,
                        providers=providers,
                    )
                continue
            quote_guard = None
            guard_policy = getattr(CAPABILITY_SOURCES.source(provider_id), 'quote_guard_policy', None) if quote_request else None
            if guard_policy is not None:
                quote_guard = guard_policy.prepare(QuoteGuardContext(self.store, instrument_type,
                    upstream_source, logged_symbol, effective_policy.get('revision')))
                if quote_guard is None:
                    last_error = ProviderCallError("not_admitted", guard_policy.rejected_message)
                    continue
            if budgeted_quote:
                budget = self.store.claim_provider_request_budget(
                    provider_id,
                    capability,
                    instrument_type,
                    str(budget_symbol),
                    upstream_source=upstream_source,
                    request_id=request_id,
                )
                if not budget["allowed"]:
                    last_error = ProviderCallError(
                        "request_budget_cooldown",
                        "该 Provider 的 ETF 单标请求仍在冷却期",
                        diagnostics={
                            "provider": provider_id,
                            "requestKey": budget["requestKey"],
                            "remainingSeconds": budget["remainingSeconds"],
                            "budgetSeconds": 600,
                        },
                    )
                    if quote_request:
                        _log_quote_event(
                            stage="provider-qualification",
                            request_id=request_id,
                            symbol=logged_symbol,
                            provider_symbol=budget_symbol,
                            instrument_type=instrument_type,
                            status="rejected",
                            duration_ms=(time.monotonic() - quote_started) * 1000,
                            provider=provider_id,
                            error_code=last_error.code,
                            providers=providers,
                        )
                    continue
            # An ETF reservation permits only one upstream call.
            attempts = 1
            for attempt in range(attempts):
                started = self.clock()
                quote_call_started = time.monotonic()
                if quote_request:
                    _log_quote_event(
                        stage="provider-call",
                        request_id=request_id,
                        symbol=logged_symbol,
                        provider_symbol=budget_symbol,
                        instrument_type=instrument_type,
                        status="started",
                        provider=provider_id,
                        providers=providers,
                        attempt=attempt + 1,
                    )
                try:
                    if provider_id not in attempted_providers:
                        attempted_providers.append(provider_id)
                    attempted_targets.append(target)
                    if quote_guard is None:
                        adapter = self._adapter(provider_id)
                    else:
                        adapter = self._adapter(provider_id, quote_guard.snapshot,
                            market_v3_route_key=current_data_route_key(capability, instrument_type, logged_symbol),
                        )
                    result = operation(provider_id, adapter, upstream_source)
                    if result is None:
                        raise ProviderCallError("not_covered", "Provider 未覆盖该标的")
                    if quote_guard is not None and quote_guard.still_current() is not True:
                        raise ProviderCallError("not_admitted", guard_policy.changed_message)
                    if quote_request:
                        _log_quote_event(
                            stage="provider-call",
                            request_id=request_id,
                            symbol=logged_symbol,
                            provider_symbol=budget_symbol,
                            instrument_type=instrument_type,
                            status="success",
                            duration_ms=(time.monotonic() - quote_call_started) * 1000,
                            provider=provider_id,
                            providers=providers,
                            attempt=attempt + 1,
                        )
                    elapsed_ms = max(0, int((self.clock() - started) * 1000))
                    self.circuit.success(key)
                    self.store.record_health(
                        provider_id,
                        capability,
                        instrument_type,
                        state="healthy",
                        circuit="closed",
                        consecutive_failures=0,
                        latency_ms=elapsed_ms,
                        upstream_source=upstream_source,
                    )
                    manifest = getattr(self.store, "provider_registry", lambda: [])()
                    provider_revision = "unknown"
                    for item in manifest:
                        if item.get("providerId") == provider_id:
                            provider_revision = (
                                f"{provider_id}:manifest:{item.get('version', 1)}:config:{item.get('configVersion', 0)}"
                            )
                            break
                    return ProviderExecution(
                        value=result,
                        capability=capability,
                        instrument_type=instrument_type,
                        provider=provider_id,
                        fallback_used=fallback_used,
                        effective_policy=effective_policy,
                        route=tuple(providers),
                        attempted_providers=tuple(attempted_providers),
                        upstream_source=upstream_source,
                        route_index=route_index,
                        provider_revision=provider_revision,
                        route_targets=tuple(route_targets),
                        attempted_targets=tuple(attempted_targets),
                    )
                except ProviderCallError as exc:
                    last_error = exc
                    if quote_request:
                        _log_quote_event(
                            stage="provider-call",
                            request_id=request_id,
                            symbol=logged_symbol,
                            provider_symbol=budget_symbol,
                            instrument_type=instrument_type,
                            status="failure",
                            duration_ms=(time.monotonic() - quote_call_started) * 1000,
                            provider=provider_id,
                            error_code=exc.code,
                            providers=providers,
                            attempt=attempt + 1,
                        )
                except (TimeoutError, ConnectionError, OSError):
                    last_error = ProviderCallError(
                        "transient_failure", "Provider 暂时不可用", retryable=True
                    )
                    if quote_request:
                        _log_quote_event(
                            stage="provider-call",
                            request_id=request_id,
                            symbol=logged_symbol,
                            provider_symbol=budget_symbol,
                            instrument_type=instrument_type,
                            status="failure",
                            duration_ms=(time.monotonic() - quote_call_started) * 1000,
                            provider=provider_id,
                            error_code=last_error.code,
                            providers=providers,
                            attempt=attempt + 1,
                        )
                    logger.warning("ThesisLedger Provider transient failure")
                except Exception as exc:  # adapter boundary; do not expose raw error.
                    code = classify_provider_exception(exc)
                    last_error = ProviderCallError(code, "Provider 请求失败")
                    if quote_request:
                        _log_quote_event(
                            stage="provider-call",
                            request_id=request_id,
                            symbol=logged_symbol,
                            provider_symbol=budget_symbol,
                            instrument_type=instrument_type,
                            status="failure",
                            duration_ms=(time.monotonic() - quote_call_started) * 1000,
                            provider=provider_id,
                            error_code=code,
                            providers=providers,
                            attempt=attempt + 1,
                        )
                    logger.warning(
                        "ThesisLedger Provider failure provider=%s capability=%s code=%s",
                        provider_id,
                        capability,
                        code,
                    )
                if not last_error.retryable or attempt + 1 >= attempts:
                    break
            if last_error and last_error.retryable:
                circuit_state, failures = self.circuit.failure(key, self.clock())
                self.store.record_health(
                    provider_id,
                    capability,
                    instrument_type,
                    state="degraded",
                    circuit=circuit_state,
                    consecutive_failures=failures,
                    error_code=last_error.code,
                    upstream_source=upstream_source,
                )
            elif last_error:
                self.circuit.success(key)
                self.store.record_health(
                    provider_id,
                    capability,
                    instrument_type,
                    state="degraded",
                    circuit="closed",
                    consecutive_failures=0,
                    error_code=last_error.code,
                    upstream_source=upstream_source,
                )
        if last_error is None:
            raise ProviderCallError("upstream_unavailable", "Provider 暂时不可用")
        if last_error.code == "circuit_open" and not attempted_providers:
            raise NoEligibleProviderError(capability, instrument_type)
        if not budgeted_quote:
            exact_bar = (capability == "DAILY_BAR" and isinstance(effective_policy_override, Mapping)
                         and effective_policy_override.get("contractVersion") == 3)
            if not exact_bar and capability not in LOCAL_RULE_CAPABILITIES and last_error.code in {"not_covered", "unsupported", "circuit_open"}:
                raise ProviderCallError(
                    "upstream_unavailable",
                    "没有 Provider 返回可用数据",
                )
            raise last_error
        final_diagnostics = {
            "route": list(providers),
            "attemptedProviders": attempted_providers,
            "attemptedTargets": [
                {
                    "providerId": target.provider_id,
                    "upstreamSource": target.upstream_source,
                }
                for target in attempted_targets
            ],
            **last_error.diagnostics,
        }
        if last_error.code in {"not_covered", "unsupported", "circuit_open"}:
            raise ProviderCallError(
                "upstream_unavailable",
                "没有 Provider 返回可用数据",
                diagnostics=final_diagnostics,
            )
        raise ProviderCallError(
            last_error.code,
            str(last_error),
            retryable=last_error.retryable,
            diagnostics=final_diagnostics,
        )

    def execute_market_bars_v3(
        self,
        request: ThesisLedgerDataRequest,
        route_key: Mapping[str, Any],
        *,
        route_target: Mapping[str, Any] | None = None,
    ) -> ProviderExecution:
        """Fetch one exact V3 bar route without consulting the V1/V2 policy."""
        if not isinstance(request, ThesisLedgerDataRequest):
            raise TypeError("request 必须是 ThesisLedgerDataRequest")
        if (
            request.capability != "DAILY_BAR"
            or request.timeframe != "1d"
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

        effective_policy = self.store.effective_policy_v3()
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
                self._current_market_v3_admission, request, route_key, exact_target,
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
            from src.services.thesis_ledger_exact_bar_execution import execute_exact_bar

            return execute_exact_bar(self, request, route_key, effective_policy, provider_id, adapter, upstream_source)

        try:
            return self._execute_with_metadata(
                "DAILY_BAR",
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

    def market_route_catalog_v3(self, *, event_admissions=False) -> dict[str, Any]:
        from src.services.thesis_ledger_route_catalog import build_runtime_route_catalog
        return build_runtime_route_catalog(self, event_admissions=event_admissions)

    _catalog_manifest_matches = staticmethod(catalog_manifest_matches_v3)

    def execute_request(self, request: ThesisLedgerDataRequest) -> ProviderExecution:
        """通过当前路由执行请求并返回来源与修订元数据。"""
        if not isinstance(request, ThesisLedgerDataRequest):
            raise TypeError("request 必须是 ThesisLedgerDataRequest")

        capability = request.capability
        instrument_type = request.instrument_type or instrument_type_for_symbol(request.symbol)
        reference_operation = reference_data_operation(request, ProviderCallError, self)
        if reference_operation is not None:
            return self._execute_request_with_boundary(request, capability, instrument_type, reference_operation)
        if capability == "REALTIME_QUOTE":
            provider_symbol = provider_symbol_for_contract(request.symbol)

            def operation(provider_id: str, adapter: Any, upstream_source: str | None = None) -> Any:
                value = self._realtime_quote(
                    adapter, provider_id,
                    request.symbol,
                    instrument_type, upstream_source,
                )
                if value is None:
                    raise ProviderCallError("not_covered", "Provider 未覆盖该标的")
                return self._validate_quote(value)

            return self._execute_request_with_boundary(
                request,
                capability,
                instrument_type,
                operation,
                budget_symbol=provider_symbol if instrument_type == "ETF" else None,
                request_id=request.request_id,
            )

        if capability == "DAILY_BAR":
            if request.timeframe not in (None, "1d"):
                raise ThesisLedgerGatewayError(
                    "unsupported_capability",
                    "当前日线合同只支持 1d bars",
                    request,
                )
            def operation(provider_id: str, adapter: Any, upstream_source: str | None = None) -> Any:
                raise ProviderCallError("unsupported_capability", "日线读取需要显式 V3 bar 目标")

            return self._execute_request_with_boundary(
                request,
                capability,
                instrument_type,
                operation,
            )

        if capability == "FUND_NAV":
            from src.services.thesis_ledger_fund_nav_query import read_latest_nav
            def latest_nav(provider_id: str, adapter: Any, upstream_source=None) -> Any:
                return read_latest_nav(request, provider_id, adapter, upstream_source, ProviderCallError)

            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                latest_nav,
            )

        if capability == "FUND_NAV_HISTORY":
            from src.services.thesis_ledger_fund_nav_history_query import read_nav_history
            def history_nav(provider_id: str, adapter: Any, upstream_source=None) -> Any:
                return read_nav_history(request, provider_id, adapter, upstream_source, ProviderCallError)

            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                history_nav,
            )

        if capability == "FUND_HOLDINGS":
            from .thesis_ledger_fund_holdings_query import read_holdings
            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                lambda provider_id, adapter, upstream_source=None: read_holdings(
                    request, provider_id, adapter, upstream_source, ProviderCallError),
            )

        if capability == "CHIP_SUMMARY":
            if instrument_type != "STOCK":
                raise ThesisLedgerGatewayError(
                    "unsupported_capability",
                    "当前筹码合同只支持 STOCK 的 CHIP_SUMMARY",
                    request,
                )
            from .thesis_ledger_chip_summary_query import read_chip

            return self._execute_request_with_boundary(
                request,
                capability,
                instrument_type,
                lambda provider, adapter, upstream=None: read_chip(request, provider, adapter, upstream, ProviderCallError),
            )

        raise ThesisLedgerGatewayError(
            "unsupported_capability",
            f"Capability {capability} 尚未接入 ThesisLedger Provider runtime",
            request,
        )

    def _execute_request_with_boundary(
        self, request, capability, instrument_type, operation,
        *, budget_symbol=None, request_id=None,
    ):
        from src.services.thesis_ledger_current_data_execution import execute_current_data_request

        return execute_current_data_request(
            self, request, capability, instrument_type, operation,
            budget_symbol=budget_symbol, request_id=request_id,
        )


    def smoke(
        self,
        provider_id: str,
        capability: str,
        *,
        credential_snapshot: ProviderCredentialSnapshot | None = None,
        draft_probe: bool | None = None,
    ) -> dict[str, Any]:
        from src.services.thesis_ledger_provider_smoke import smoke_provider

        return smoke_provider(
            self, provider_id, capability,
            credential_snapshot=credential_snapshot, draft_probe=draft_probe,
        )


@dataclass(frozen=True)
class ThesisLedgerDataResult:
    """ThesisLedger 数据能力共用的现行结果。"""

    request: ThesisLedgerDataRequest
    data: Any
    provider: str
    fallback_used: bool
    effective_policy: Mapping[str, Any] | None
    route: tuple[str, ...]
    attempted_providers: tuple[str, ...]
    served_from_cache: bool = False
    upstream_source: str | None = None
    route_index: int = 0
    provider_revision: str = "unknown"
    route_targets: tuple[RouteTarget, ...] = ()

    @property
    def value(self) -> Any:
        """Alias for callers that use the runtime's value terminology."""
        return self.data

    @property
    def capability(self) -> str:
        """Return the normalized request capability."""
        return self.request.capability

    @property
    def effective_revision(self) -> int | None:
        """Return the Effective Policy revision used for this result."""
        if not self.effective_policy:
            return None
        revision = self.effective_policy.get("revision")
        return int(revision) if isinstance(revision, int) else None

    @property
    def source_desired_revision(self) -> int | None:
        """Return the Desired Policy revision projected into this result."""
        if not self.effective_policy:
            return None
        revision = self.effective_policy.get("sourceDesiredRevision")
        return int(revision) if isinstance(revision, int) else None

    @property
    def provenance(self) -> dict[str, Any]:
        """Return provider, fallback and policy provenance metadata."""
        return {
            "provider": self.provider,
            "servedFromCache": self.served_from_cache,
            "fallbackUsed": self.fallback_used,
            "attemptedProviders": list(self.attempted_providers),
            "effectiveRevision": self.effective_revision,
            "sourceDesiredRevision": self.source_desired_revision,
        }

    def as_dict(self) -> dict[str, Any]:
        """Return a transport-neutral result projection for future facades."""
        return {
            "data": self.data,
            "provider": self.provider,
            "fallbackUsed": self.fallback_used,
            "servedFromCache": self.served_from_cache,
            "provenance": self.provenance,
            "effectivePolicy": self.effective_policy,
            "requestId": self.request.request_id,
        }


class ThesisLedgerDataGateway:
    """数据能力统一入口；扩展处理器必须保留 ProviderExecution 来源信息。"""

    DECLARED_CAPABILITIES = (
        "REALTIME_QUOTE",
        "DAILY_BAR",
        "FUND_NAV",
        "FUND_NAV_HISTORY",
        "FUND_HOLDINGS",
        "INDICATOR",
        "CHIP_SUMMARY",
    )

    def __init__(
        self,
        runtime: ThesisLedgerProviderRuntime | None = None,
        *,
        handlers: Mapping[str, Callable[[ThesisLedgerDataRequest], ProviderExecution]] | None = None,
    ) -> None:
        self.runtime = runtime or get_thesis_ledger_runtime()
        self._handlers: dict[str, Callable[[ThesisLedgerDataRequest], ProviderExecution]] = {}
        for capability, handler in (handlers or {}).items():
            self.register_handler(capability, handler)

    def register_handler(
        self,
        capability: str,
        handler: Callable[[ThesisLedgerDataRequest], ProviderExecution],
    ) -> None:
        """Register a metadata-preserving handler for a declared capability."""
        normalized = str(capability or "").strip().upper()
        if normalized not in self.DECLARED_CAPABILITIES:
            raise ValueError(f"未声明的 ThesisLedger Capability: {capability}")
        if not callable(handler):
            raise TypeError("handler 必须可调用")
        self._handlers[normalized] = handler

    @staticmethod
    def _coerce_request(
        request: ThesisLedgerDataRequest | Mapping[str, Any],
        kwargs: Mapping[str, Any],
    ) -> ThesisLedgerDataRequest:
        if isinstance(request, ThesisLedgerDataRequest):
            if kwargs:
                raise ProviderCallError(
                    "invalid_request",
                    "ThesisLedgerDataRequest 不应同时传入额外字段",
                )
            return request
        if not isinstance(request, Mapping):
            raise ProviderCallError("invalid_request", "request 必须是对象")
        payload = dict(request)
        payload.update(kwargs)
        return ThesisLedgerDataRequest(**payload)

    @staticmethod
    def _result_from_execution(
        request: ThesisLedgerDataRequest,
        execution: ProviderExecution,
    ) -> ThesisLedgerDataResult:
        return ThesisLedgerDataResult(
            request=request,
            data=execution.value,
            provider=execution.provider,
            fallback_used=execution.fallback_used,
            effective_policy=execution.effective_policy,
            route=execution.route,
            attempted_providers=execution.attempted_providers,
            upstream_source=execution.upstream_source,
            route_index=execution.route_index,
            provider_revision=execution.provider_revision,
            route_targets=execution.route_targets,
        )

    def fetch(
        self,
        request: ThesisLedgerDataRequest | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> ThesisLedgerDataResult:
        """Fetch one capability with standard request/result semantics."""
        if request is None:
            request = kwargs
            kwargs = {}
        try:
            normalized = self._coerce_request(request, kwargs)
        except ProviderCallError as exc:
            payload = dict(request) if isinstance(request, Mapping) else {}
            fallback_request = ThesisLedgerDataRequest(
                str(payload.get("capability") or "UNKNOWN"),
                str(payload.get("symbol") or "_invalid"),
                request_id=str(payload.get("request_id") or uuid.uuid4()),
            )
            raise ThesisLedgerGatewayError(
                exc.code,
                str(exc),
                fallback_request,
                retryable=exc.retryable,
            ) from exc
        handler = self._handlers.get(normalized.capability)
        try:
            execution = (
                handler(normalized)
                if handler is not None
                else self.runtime.execute_request(normalized)
            )
        except ThesisLedgerGatewayError:
            raise
        except ProviderCallError as exc:
            raise ThesisLedgerGatewayError(
                exc.code,
                str(exc),
                normalized,
                retryable=exc.retryable,
            ) from exc
        except Exception as exc:  # handler boundary; raw Provider details stay in logs.
            logger.warning(
                "ThesisLedger gateway handler failed capability=%s: %s",
                normalized.capability,
                exc,
            )
            raise ThesisLedgerGatewayError(
                "upstream_failure",
                "Provider 请求失败",
                normalized,
            ) from exc
        if not isinstance(execution, ProviderExecution):
            raise ThesisLedgerGatewayError(
                "invalid_response",
                "Gateway handler 必须返回 ProviderExecution",
                normalized,
            )
        return self._result_from_execution(normalized, execution)

    execute = fetch
    request = fetch

    def quote(self, symbol: str, **kwargs: Any) -> ThesisLedgerDataResult:
        """Fetch realtime Quote through the standard gateway boundary."""
        return self.fetch(ThesisLedgerDataRequest("REALTIME_QUOTE", symbol, **kwargs))

    def bars(self, symbol: str, **kwargs: Any) -> ThesisLedgerDataResult:
        """Fetch Daily Bar data through the standard gateway boundary."""
        return self.fetch(ThesisLedgerDataRequest("DAILY_BAR", symbol, **kwargs))

    def fund_nav(self, symbol: str, **kwargs: Any) -> ThesisLedgerDataResult:
        """Fetch one Fund NAV result through the standard gateway boundary."""
        return self.fetch(ThesisLedgerDataRequest("FUND_NAV", symbol, **kwargs))

    def fund_nav_history(self, symbol: str, **kwargs: Any) -> ThesisLedgerDataResult:
        """Fetch a complete Fund NAV history sequence through the gateway."""
        return self.fetch(ThesisLedgerDataRequest("FUND_NAV_HISTORY", symbol, **kwargs))

    def fund_holdings(self, symbol: str, **kwargs: Any) -> ThesisLedgerDataResult:
        """Fetch the latest disclosed Fund holdings through the gateway."""
        return self.fetch(ThesisLedgerDataRequest("FUND_HOLDINGS", symbol, **kwargs))

    def chip_summary(self, symbol: str, **kwargs: Any) -> ThesisLedgerDataResult:
        """Fetch one complete CHIP_SUMMARY through the Effective Policy route."""
        return self.fetch(ThesisLedgerDataRequest("CHIP_SUMMARY", symbol, **kwargs))


_runtime: ThesisLedgerProviderRuntime | None = None
_runtime_database_path: str | None = None
_gateway: ThesisLedgerDataGateway | None = None
_gateway_runtime: ThesisLedgerProviderRuntime | None = None


def get_thesis_ledger_runtime() -> ThesisLedgerProviderRuntime:
    global _runtime, _runtime_database_path
    database_path = os.getenv("DATABASE_PATH", "./data/stock_analysis.db").strip() or "./data/stock_analysis.db"
    if _runtime is None or _runtime_database_path != database_path:
        _runtime = ThesisLedgerProviderRuntime()
        _runtime_database_path = database_path
    return _runtime


def get_thesis_ledger_data_gateway() -> ThesisLedgerDataGateway:
    """Return the process-local gateway paired with the current runtime."""
    global _gateway, _gateway_runtime
    runtime = get_thesis_ledger_runtime()
    if _gateway is None or _gateway_runtime is not runtime:
        _gateway = ThesisLedgerDataGateway(runtime)
        _gateway_runtime = runtime
    return _gateway


get_thesis_ledger_gateway = get_thesis_ledger_data_gateway
