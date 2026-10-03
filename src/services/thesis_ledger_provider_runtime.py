# -*- coding: utf-8 -*-
"""DSA ThesisLedger consumer namespace 的 Provider 路由执行器。

它只按 DSA SQLite 中的 Effective Policy 顺序执行。旧的
``DataFetcherManager`` 仍服务 DSA native analysis；本模块不把 native 的默认
优先级或隐藏 fallback 泄漏到 ThesisLedger consumer。
"""

from __future__ import annotations

import importlib
import hashlib
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
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_oauth_contract import OAuthStateError
from src.services.thesis_ledger_nav_dates import nav_datetime, select_nav_rows, validate_nav_history
from src.services.thesis_ledger_holding_rows import validate_holding_rows
from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS,
    ControlContractError,
    ThesisLedgerControlStore,
    _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_market_v3_adapters import (
    HITHINK_STOCK_HISTORY_SOURCE,
    HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1,
    iter_market_v3_bar_adapters,
    iter_market_v3_gated_bar_adapters,
    market_v3_bar_adapter_reason,
    basic_market_price_route,
)
from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
    market_calendar_evidence_v3,
    market_listing_fact_v3,
    market_pagination_contract_v3,
)
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_price_route_access import (
    basic_price_credential_ready, basic_price_route_configured, price_route_admission,
)
from src.services.thesis_ledger_market_v3_pagination import MarketPaginationError, market_pagination_proof_v3
from src.services.thesis_ledger_event_v3_adapters import iter_event_adapters
from src.services.thesis_ledger_catalog_manifest_v3 import catalog_manifest_matches_v3
from src.services.thesis_ledger_rqdata_event_v3 import current_rqdata_event_admission
from src.services.thesis_ledger_tushare_event_v3 import current_tushare_event_admission
from src.services.thesis_ledger_hithink_event_v3 import current_hithink_event_admission
from src.services.thesis_ledger_hithink_quote_runtime import (
    current_hithink_quote_guard, hithink_quote_guard_still_current,
)
from src.services.thesis_ledger_hithink_quote_admission import (
    hithink_quote_admission_matches_current, hithink_quote_revisions,
    iter_hithink_quote_routes,
)
from src.services.thesis_ledger_current_data_route import (
    CurrentDataRouteError, current_data_route_key, iter_current_data_adapters,
    select_current_data_targets,
)
from src.services.thesis_ledger_quote_dispatch import realtime_quote as dispatch_realtime_quote

from src.services import thesis_ledger_market_v3_revisions as _route_revisions

# Preserve the existing import surface while revision ownership moves out of the executor.
MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION = _route_revisions.MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION
market_v3_current_route_revisions = _route_revisions.market_v3_current_route_revisions
_market_v3_admission_matches_current = _route_revisions._market_v3_admission_matches_current

logger = logging.getLogger(__name__)

_PROVIDER_ADAPTER_IMPORTS = {
    "akshare": ("data_provider.akshare_fetcher", "AkshareFetcher"),
    "efinance": ("data_provider.efinance_fetcher", "EfinanceFetcher"),
    "tencent": ("data_provider.tencent_fetcher", "TencentFetcher"),
    "tushare": ("data_provider.tushare_fetcher", "TushareFetcher"),
    "tickflow": ("data_provider.tickflow_fetcher", "TickFlowFetcher"),
    "pytdx": ("data_provider.pytdx_fetcher", "PytdxFetcher"),
    "baostock": ("data_provider.baostock_fetcher", "BaostockFetcher"),
    "yfinance": ("data_provider.yfinance_fetcher", "YfinanceFetcher"),
    "longbridge": ("data_provider.longbridge_fetcher", "LongbridgeFetcher"),
    "finnhub": ("data_provider.finnhub_fetcher", "FinnhubFetcher"),
    "alphavantage": ("data_provider.alphavantage_fetcher", "AlphaVantageFetcher"),
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


def provider_symbol_for_contract(symbol: str) -> str:
    """将 Contract 标的转换为现有 Provider 适配器接受的代码格式。"""
    value = symbol.strip().upper()
    if "." in value:
        base, suffix = value.rsplit(".", 1)
        if suffix in {"SH", "SS", "SZ", "BJ"} and base.isdigit():
            return base
        if suffix == "HK" and base.isdigit():
            return f"HK{base.zfill(5)}"
    for prefix in ("SH", "SZ", "SS", "BJ"):
        if value.startswith(prefix):
            remainder = value[len(prefix) :]
            if remainder.startswith("."):
                remainder = remainder[1:]
            if remainder.isdigit():
                return remainder
    return value


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
        self._adapter_versions: dict[str, tuple[int, int, str]] = {}
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
        if provider_id == "rqdata":
            return current_rqdata_event_admission(self.store, key, target, provider_manifest)
        if provider_id == "hithink" and key.get("capability") == "CASH_DISTRIBUTION":
            return current_hithink_event_admission(self.store, key, target, provider_manifest)
        if provider_id == "tushare" and key.get("capability") == "CASH_DISTRIBUTION":
            return current_tushare_event_admission(self.store, key, target, provider_manifest)
        if (
            provider_id == "hithink" and key.get("kind") == "data"
            and key.get("capability") == "REALTIME_QUOTE"
        ):
            if hithink_quote_revisions(
                key.get("assetType"), target.get("upstreamSource")
            ) is None:
                return None
            try:
                snapshot = credential_snapshot or self.store.provider_credential_snapshot(provider_id)
            except (AttributeError, ControlContractError, ValueError):
                return None
            credential_revision = _provider_credential_revision_from_snapshot(snapshot)
            admission = get_admission(key=dict(key), target=dict(target))
            if not isinstance(admission, Mapping):
                return None
            symbols = admission.get("scopeSymbols")
            if not isinstance(symbols, list) or not symbols:
                return None
            now = datetime.now(timezone.utc)
            if all(hithink_quote_admission_matches_current(
                admission, asset_type=key["assetType"],
                source=target["upstreamSource"], symbol=symbol,
                credential_revision=credential_revision, now=now,
            ) for symbol in symbols):
                return admission
            return None
        credential_revision = None
        if provider_id in {"hithink", "tushare"}:
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
        if basic_market_price_route(key, target):
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
        if provider_id == "hithink":
            injected = self.adapters.get(provider_id)
            if injected is not None:
                return injected
            fetch_method = self._fetch_hithink_etf_bars
            if market_v3_route_key is not None:
                if market_v3_route_key.get("assetType") == "STOCK":
                    fetch_method = self._fetch_hithink_stock_bars
                elif market_v3_route_key.get("assetType") != "ETF":
                    raise ProviderCallError("unsupported_source", "HiThink V3 未登记该 InstrumentType")
            # V3 execution rebinds this method to the exact credential snapshot
            # it compared against admission immediately before the source call.
            if snapshot is None:
                return SimpleNamespace(get_market_bars_v3=fetch_method)
            return SimpleNamespace(
                get_market_bars_v3=lambda request: fetch_method(
                    request,
                    credential_snapshot=snapshot,
                )
            )
        with self._adapter_cache_lock:
            if provider_id in self.adapters and provider_id not in self._adapter_versions:
                return self.adapters[provider_id]
        try:
            snapshot = snapshot or self.store.provider_credential_snapshot(provider_id)
            version = (snapshot.config_version, snapshot.credential_version, snapshot.source)
            if provider_id == "tushare":
                revision = _provider_credential_revision_from_snapshot(snapshot)
                version = (snapshot.config_version, snapshot.credential_version, f"{snapshot.source}:{revision}")
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
            if adapter_import is None:
                raise ProviderCallError("UNKNOWN_PROVIDER", "未知 Provider")
            module_name, class_name = adapter_import
            adapter_class = getattr(importlib.import_module(module_name), class_name)
            values = snapshot.values
            manifest = PROVIDER_MANIFESTS.get(provider_id, {})
            if manifest.get("requiresCredential") and not values and not (
                provider_id == "longbridge" and snapshot.source == "environment"
            ):
                raise ProviderCallError("not_configured", "Provider 凭证未配置")
            if provider_id == "tickflow":
                from src.config import get_config

                config = get_config()
                adapter = adapter_class(
                    api_key=values.get("apiKey"),
                    timeout=5 if probe else 30.0,
                    kline_adjust=getattr(config, "tickflow_kline_adjust", "none"),
                    batch_daily_enabled=getattr(config, "tickflow_batch_daily_enabled", True),
                    batch_size=getattr(config, "tickflow_batch_size", 100),
                    priority=getattr(config, "tickflow_priority", 2),
                )
            elif provider_id == "tushare":
                adapter = adapter_class(
                    token=values.get("token"),
                    request_timeout=5 if probe else 30,
                    http_url=values.get("httpUrl"),
                )
            elif provider_id in {"finnhub", "alphavantage"}:
                adapter = adapter_class(
                    api_key=values.get("apiKey"),
                    strict_errors=snapshot.source == "control",
                )
            elif provider_id == "longbridge" and snapshot.method == "oauth":
                from src.services.provider_oauth_runtime import build_page_oauth

                oauth_token = build_page_oauth(snapshot, self.store.database_path)
                adapter = adapter_class(oauth_token=oauth_token)
            elif provider_id == "longbridge" and values:
                adapter = adapter_class(
                    app_key=values.get("appKey"),
                    app_secret=values.get("appSecret"),
                    access_token=values.get("accessToken"),
                )
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
        if isinstance(adapter_result, tuple):
            if len(adapter_result) != 2:
                raise ProviderCallError("invalid_response", "Provider 日线响应结构非法")
            return adapter_result[0]
        return adapter_result

    def _fetch_hithink_etf_bars(
        self,
        request: ThesisLedgerDataRequest,
        *,
        credential_snapshot: ProviderCredentialSnapshot | None = None,
    ) -> Any:
        """Lazily invoke the exact HiThink ETF adapter after V3 admission checks."""
        if (
            request.capability != "DAILY_BAR"
            or request.instrument_type != "ETF"
            or request.timeframe != "1d"
            or request.adjustment != "qfq"
            or request.start is None
            or request.end is None
        ):
            raise ProviderCallError("unsupported_source", "HiThink V3 仅登记 ETF qfq 日线")

        listing = market_listing_fact_v3(request.symbol)
        calendar = market_calendar_evidence_v3("CN", request.start, request.end)
        if (
            listing is None
            or calendar is None
            or not isinstance(calendar.get("expectedSessionDates"), list)
        ):
            raise ProviderCallError("not_covered", "HiThink ETF 缺少本地范围证据")

        snapshot = credential_snapshot
        if snapshot is None:
            raise ProviderCallError("not_configured", "HiThink API 凭据未与当前 admission 复核")
        api_key = snapshot.values.get("apiKey")
        if (
            snapshot.source != "environment"
            or snapshot.method != "api_key"
            or not isinstance(api_key, str)
            or not api_key.strip()
        ):
            raise ProviderCallError("not_configured", "HiThink API 凭据未配置")

        from src.services.thesis_ledger_hithink_etf import (
            HiThinkETFAdapterError,
            HiThinkETFCalendarEvidence,
            fetch_hithink_etf_daily_bars,
        )

        calendar_evidence = HiThinkETFCalendarEvidence(
            symbol=request.symbol,
            calendar_name="SZSE",
            calendar_version=str(calendar["revision"]),
            market_sessions=tuple(calendar["expectedSessionDates"]),
            listing_date=listing["firstTradingDate"],
            listing_source=listing["source"],
            listing_source_version=listing["revision"],
        )
        try:
            result = fetch_hithink_etf_daily_bars(
                symbol=request.symbol,
                start=request.start,
                end=request.end,
                adjustment="qfq",
                api_key=api_key,
                calendar_evidence=calendar_evidence,
                timeout_seconds=min(DAILY_BAR_TARGET_TIMEOUT_SECONDS, request.parameters.get("target_timeout_seconds", DAILY_BAR_TARGET_TIMEOUT_SECONDS)),
                allow_missing_sessions=request.parameters.get("historical_tradability") is True,
            )
        except HiThinkETFAdapterError as exc:
            raise ProviderCallError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
                diagnostics=exc.diagnostics,
            ) from exc

        from src.services.thesis_ledger_hithink_etf_tradability import hithink_etf_frame
        frame = hithink_etf_frame(
            result, listing=listing, calendar=calendar,
            upstream_source=HITHINK_ETF_HISTORY_SOURCE,
            historical_tradability=request.parameters.get("historical_tradability") is True,
        )
        if result.volume_unit != "unknown" or result.turnover_unit != "unknown":
            raise ProviderCallError("invalid_response", "HiThink ETF 适配器单位合同已变更")
        from src.services.thesis_ledger_hithink_etf_units import hithink_etf_field_contract
        from src.services.thesis_ledger_market_v3_facts import HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1

        frame.attrs["hithink_etf_field_units"] = hithink_etf_field_contract(
            symbol=request.symbol,
            start=request.start,
            end=request.end,
            source_revision=HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
        )
        return frame

    def _fetch_hithink_stock_bars(
        self,
        request: ThesisLedgerDataRequest,
        *,
        credential_snapshot: ProviderCredentialSnapshot | None = None,
    ) -> Any:
        """Invoke the explicit HiThink stock adjustment adapter after admission."""
        if (
            request.capability != "DAILY_BAR"
            or request.instrument_type != "STOCK"
            or request.timeframe != "1d"
            or request.adjustment not in {"none", "qfq", "hfq"}
            or request.start is None
            or request.end is None
        ):
            raise ProviderCallError(
                "unsupported_source",
                "HiThink 股票 V3 仅登记 none/qfq/hfq 日线",
            )
        calendar = market_calendar_evidence_v3("CN", request.start, request.end)
        if calendar is None or not isinstance(calendar.get("expectedSessionDates"), list):
            raise ProviderCallError("not_covered", "HiThink 股票缺少本地范围证据")

        if credential_snapshot is None:
            raise ProviderCallError("not_configured", "HiThink API 凭据未与当前 admission 复核")
        api_key = credential_snapshot.values.get("apiKey")
        if (
            credential_snapshot.provider_id != "hithink"
            or credential_snapshot.source != "environment"
            or credential_snapshot.method != "api_key"
            or not isinstance(api_key, str)
            or not api_key.strip()
        ):
            raise ProviderCallError("not_configured", "HiThink API 凭据未配置")

        from src.services.thesis_ledger_hithink_stock import (
            HiThinkStockError,
            HiThinkStockHistoricalAdapter,
        )

        adapter = HiThinkStockHistoricalAdapter(
            api_key=api_key,
            timeout_seconds=DAILY_BAR_TARGET_TIMEOUT_SECONDS,
        )
        try:
            return adapter.fetch_daily_bars(
                request.symbol,
                request.start,
                request.end,
                request.adjustment,
                expected_sessions=tuple(calendar["expectedSessionDates"]),
                calendar_revision=str(calendar["revision"]),
            )
        except HiThinkStockError as exc:
            raise ProviderCallError(
                exc.code,
                str(exc),
                retryable=exc.retryable,
            ) from exc

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
            number = float(value)
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
        price = cls._finite_number(getattr(value, "price", None), "price", positive=True)
        previous_close = getattr(value, "pre_close", None)
        if previous_close is None:
            change_amount = getattr(value, "change_amount", None)
            if change_amount is not None:
                try:
                    derived = price - float(change_amount)
                except (TypeError, ValueError):
                    derived = None
                if derived is not None and math.isfinite(derived) and derived >= 0:
                    previous_close = derived
            if previous_close is None:
                change_pct = getattr(value, "change_pct", None)
                if change_pct is not None:
                    try:
                        divisor = 1.0 + float(change_pct) / 100.0
                        derived = price / divisor if divisor > 0 else None
                    except (TypeError, ValueError, ZeroDivisionError):
                        derived = None
                    if derived is not None and math.isfinite(derived) and derived >= 0:
                        previous_close = derived
        if previous_close is None:
            raise ProviderCallError("invalid_response", "Provider 响应字段 previousClose 缺失")
        if getattr(value, "pre_close", None) is None:
            try:
                setattr(value, "pre_close", previous_close)
            except (AttributeError, TypeError) as exc:
                raise ProviderCallError(
                    "invalid_response", "Provider 响应对象无法补齐 previousClose"
                ) from exc
        fields = {
            "open": getattr(value, "open_price", None),
            "high": getattr(value, "high", None),
            "low": getattr(value, "low", None),
            "price": price,
            "previousClose": previous_close,
            "volume": getattr(value, "volume", None),
            "amount": getattr(value, "amount", None),
        }
        normalized = {
            name: cls._finite_number(raw, name, positive=name == "price")
            for name, raw in fields.items()
        }
        if normalized["high"] < max(
            normalized["open"], normalized["low"], normalized["price"]
        ) or normalized["low"] > min(
            normalized["open"], normalized["high"], normalized["price"]
        ):
            raise ProviderCallError("invalid_response", "Provider Quote OHLC 非法")
        return value

    @classmethod
    def _validate_bars(cls, frame: Any) -> Any:
        if frame is None or getattr(frame, "empty", True):
            raise ProviderCallError("not_covered", "Provider 未覆盖该标的")
        required = {"date", "open", "high", "low", "close", "volume", "amount"}
        if not required.issubset(set(getattr(frame, "columns", []))):
            raise ProviderCallError("invalid_response", "Provider Bar 响应缺少必要字段")
        timestamps: set[str] = set()
        for _, row in frame.iterrows():
            timestamp = str(row.get("date"))
            if not timestamp or timestamp in timestamps:
                raise ProviderCallError("invalid_response", "Provider Bar 时间重复或缺失")
            timestamps.add(timestamp)
            values = {
                name: cls._finite_number(row.get(name), name)
                for name in ("open", "high", "low", "close", "volume", "amount")
            }
            if values["high"] < max(values["open"], values["close"], values["low"]):
                raise ProviderCallError("invalid_response", "Provider Bar 最高价非法")
            if values["low"] > min(values["open"], values["close"], values["high"]):
                raise ProviderCallError("invalid_response", "Provider Bar 最低价非法")
        return frame

    @classmethod
    def _validate_fund_nav(cls, frame: Any) -> Any:
        if frame is None or getattr(frame, "empty", True):
            raise ProviderCallError("not_covered", "Provider 未覆盖该基金")
        date_columns = ("净值日期", "日期", "date", "nav_date")
        nav_columns = ("单位净值", "单位净值(元)", "unit_nav", "nav")
        columns = set(getattr(frame, "columns", []))
        date_column = next((column for column in date_columns if column in columns), None)
        nav_column = next((column for column in nav_columns if column in columns), None)
        if date_column is None or nav_column is None:
            raise ProviderCallError("invalid_response", "Provider 净值响应缺少必要字段")
        dates: set[str] = set()
        for _, row in frame.iterrows():
            try:
                date_value = nav_datetime(row.get(date_column)).isoformat()
            except (TypeError, ValueError) as exc:
                raise ProviderCallError("invalid_response", "Provider 净值日期格式非法") from exc
            if date_value in dates:
                raise ProviderCallError("invalid_response", "Provider 净值日期缺失或重复")
            dates.add(date_value)
            cls._finite_number(row.get(nav_column), "unitNav", positive=True)
        return frame

    @classmethod
    def _validate_fund_nav_history(cls, frame: Any) -> Any:
        """复用单条净值字段校验并额外保证历史序列严格升序。"""
        cls._validate_fund_nav(frame)
        columns = set(getattr(frame, "columns", []))
        date_column = next(
            (column for column in ("净值日期", "日期", "date", "nav_date") if column in columns),
            None,
        )
        nav_column = next(
            (column for column in ("单位净值", "单位净值(元)", "unit_nav", "nav") if column in columns),
            None,
        )
        if date_column is None or nav_column is None:
            raise ProviderCallError("invalid_response", "Provider 净值响应缺少必要字段")
        validate_fund_nav_history_rows(
            [(row.get(date_column), row.get(nav_column)) for _, row in frame.iterrows()]
        )
        return frame

    @classmethod
    def _validate_fund_holdings(cls, frame: Any) -> Any:
        """校验基金持仓披露，不归一或放大 Provider 权重。"""
        if frame is None or getattr(frame, "empty", True):
            raise ProviderCallError("not_covered", "Provider 未返回基金持仓披露")
        try:
            validate_holding_rows(frame)
        except (TypeError, ValueError) as exc:
            raise ProviderCallError("invalid_response", str(exc)) from exc
        return frame

    @classmethod
    def _validate_chip_summary(cls, value: Any) -> Any:
        """Validate one complete chip summary before exposing it to the facade."""
        if value is None:
            raise ProviderCallError("not_covered", "Provider 未返回筹码摘要")
        cls._finite_number(getattr(value, "avg_cost", None), "averageCost", positive=True)
        profit_ratio = cls._finite_number(getattr(value, "profit_ratio", None), "profitRatio")
        concentration_90 = cls._finite_number(
            getattr(value, "concentration_90", None),
            "concentration",
        )
        concentration_70 = cls._finite_number(
            getattr(value, "concentration_70", None),
            "concentration70",
        )
        if profit_ratio > 100 or concentration_90 > 100 or concentration_70 > 100:
            raise ProviderCallError("invalid_response", "Provider 筹码比例字段非法")
        for field_name in (
            "cost_70_low",
            "cost_70_high",
            "cost_90_low",
            "cost_90_high",
        ):
            cls._finite_number(
                getattr(value, field_name, None),
                field_name,
                positive=True,
            )
        return value

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
            if quote_request and provider_id == "hithink":
                quote_guard = current_hithink_quote_guard(
                    self.store, asset_type=instrument_type, source=upstream_source,
                    symbol=logged_symbol,
                    expected_policy_revision=effective_policy.get("revision"),
                )
                if quote_guard is None:
                    last_error = ProviderCallError("not_admitted", "HiThink 报价缺少当前精确准入")
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
                    adapter = self._adapter(provider_id)
                    if quote_guard is not None:
                        from src.services.thesis_ledger_hithink_quote import HiThinkSnapshotAdapter

                        adapter = self.adapters.get(provider_id) or HiThinkSnapshotAdapter(
                            api_key=quote_guard.snapshot.values["apiKey"],
                        )
                    result = operation(provider_id, adapter, upstream_source)
                    if result is None:
                        raise ProviderCallError("not_covered", "Provider 未覆盖该标的")
                    if quote_guard is not None and not hithink_quote_guard_still_current(
                        self.store, quote_guard, asset_type=instrument_type,
                        source=upstream_source, symbol=logged_symbol,
                    ):
                        raise ProviderCallError("not_admitted", "HiThink 报价准入已变更")
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
            if last_error.code in {"not_covered", "unsupported", "circuit_open"}:
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

        provider_symbol = provider_symbol_for_contract(request.symbol)
        requested_adjustment = request.adjustment
        requested_days = (date.fromisoformat(request.end) - date.fromisoformat(request.start)).days + 1

        def operation(provider_id: str, adapter: Any, upstream_source: str | None) -> Any:
            exact_target = {
                "providerId": provider_id,
                "upstreamSource": str(upstream_source or "").strip().lower(),
            }
            credential_snapshot = None
            if provider_id in {"hithink", "tushare"}:
                try:
                    credential_snapshot = self.store.provider_credential_snapshot(provider_id)
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
                self._current_market_v3_admission, request, route_key, exact_target,
                credential_snapshot=credential_snapshot,
            )
            if admission_reason is not None:
                raise ProviderCallError(admission_reason, "来源未就绪或请求超出该能力的审核范围")
            if provider_id in {"hithink", "tushare"}:
                verified_adapter = self._adapter(
                    provider_id,
                    snapshot=credential_snapshot,
                    cache=provider_id == "tushare",
                    market_v3_route_key=route_key,
                )
                method_name = "get_market_bars_v3" if provider_id == "hithink" else "get_daily_data_for_source"
                source_method = getattr(verified_adapter, method_name, None)
            else:
                source_method = getattr(adapter, "get_daily_data_for_source", None)
            if market_v3_bar_adapter_reason(route_key, exact_target) is not None:
                raise ProviderCallError("unsupported_adjustment", "来源未证明支持请求的价格口径")
            if not callable(source_method) or not upstream_source:
                raise ProviderCallError("unsupported_source", "来源没有精确日线适配器")
            if provider_id == "hithink":
                source_result = source_method(request)
            else:
                source_result = source_method(
                    provider_symbol, upstream_source,
                    start_date=request.start, end_date=request.end,
                    days=max(1, requested_days), adjustment=requested_adjustment,
                    timeout_seconds=DAILY_BAR_TARGET_TIMEOUT_SECONDS,
                    **({"asset_type": request.instrument_type} if provider_id == "tencent" else {}),
                )
            frame = self._daily_frame(source_result)
            attrs = getattr(frame, "attrs", None)
            declared_source = attrs.get("upstream_source") if isinstance(attrs, Mapping) else None
            if declared_source is not None and str(declared_source).strip().lower() != upstream_source:
                raise ProviderCallError("invalid_response", "来源日线响应与 RouteTarget 不一致")
            if request.parameters.get("historical_tradability") is True and getattr(frame, "empty", False) and isinstance(attrs, Mapping) and attrs.get("daily_tradability_input"):
                validated = frame
            else:
                validated = self._validate_bars(frame)
            pagination_contract = market_pagination_contract_v3(
                request.instrument_type or "",
                provider_id,
                upstream_source,
            )
            if (
                pagination_contract is None
                and provider_id == "hithink"
                and request.instrument_type == "STOCK"
                and upstream_source == HITHINK_STOCK_HISTORY_SOURCE
            ):
                pagination_contract = {
                    "protocol": HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1,
                    "maximumRows": None,
                }
            attrs = getattr(validated, "attrs", None)
            if pagination_contract is None or not isinstance(attrs, dict):
                raise ProviderCallError("invalid_response", "来源缺少已知的完整窗口读取契约")
            if provider_id == "tushare":
                if self._current_market_v3_admission(route_key, exact_target) != admission:
                    raise ProviderCallError("NO_ELIGIBLE_PROVIDER", "来源准入或凭据在读取期间发生变化")
                try:
                    market_pagination_proof_v3(
                        validated, asset_type=request.instrument_type, provider=provider_id,
                        upstream_source=upstream_source, expected_session_count=0,
                        requested_start=request.start, requested_end=request.end,
                    )
                except MarketPaginationError as error:
                    raise ProviderCallError(error.code, "来源分段传输证明无效") from None
                return validated
            pages_fetched = 1
            if provider_id == "tencent" and upstream_source == "tencent":
                retrieval = attrs.get("tencentDailyRetrieval")
                partitions = retrieval.get("partitions") if isinstance(retrieval, dict) else None
                if not isinstance(partitions, list) or not partitions:
                    raise ProviderCallError("invalid_response", "腾讯日线缺少逐年传输证据")
                pages_fetched = len(partitions)
            attrs["thesis_ledger_v3_pagination"] = {
                "status": "complete",
                "pagesFetched": pages_fetched,
                "continuationPending": False,
                "requestedStart": request.start,
                "requestedEnd": request.end,
                **pagination_contract,
            }
            return validated

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

    def market_route_catalog_v3(self) -> dict[str, Any]:
        """Return exact source-pinned V3 routes and current provider admission."""
        integrity = "complete"
        registry = self.store.provider_registry()
        if not isinstance(registry, list):
            registry = []
            integrity = "partial"

        providers: dict[str, Mapping[str, Any]] = {}
        ambiguous_providers: set[str] = set()
        for item in registry:
            if not isinstance(item, Mapping):
                integrity = "partial"
                continue
            provider_value = item.get("providerId")
            provider_id = provider_value.strip().lower() if isinstance(provider_value, str) else ""
            if not provider_id:
                integrity = "partial"
                continue
            if provider_id in providers:
                ambiguous_providers.add(provider_id)
                integrity = "partial"
                continue
            providers[provider_id] = item

        entries: list[dict[str, Any]] = []
        seen: set[str] = set()
        inventory = [
            (key, target, False) for key, target in iter_market_v3_bar_adapters()
        ]
        inventory.extend(
            (key, target, True) for key, target in iter_market_v3_gated_bar_adapters()
        )
        inventory.extend(iter_event_adapters())
        inventory.extend(
            (key, target, True) for key, target in iter_hithink_quote_routes()
        )
        inventory.extend(
            (key, target, True) for key, target in iter_current_data_adapters()
        )
        for key, target, gated in inventory:
            identity = json.dumps(
                [key, target], ensure_ascii=True, sort_keys=True, separators=(",", ":")
            )
            if identity in seen:
                integrity = "partial"
                entries = []
                break
            seen.add(identity)

            provider_id = target["providerId"]
            provider = providers.get(provider_id)
            if gated and provider is None:
                continue
            state = "not_admitted"
            if provider is None or provider_id in ambiguous_providers:
                integrity = "partial"
            elif not self._catalog_manifest_matches(provider, key, target):
                # A broad manifest drifted away from the concrete adapter inventory.
                integrity = "partial"
            elif provider.get("enabled") is False or provider.get("tombstone") is not None:
                state = "not_admitted"
            elif provider.get("enabled") is not True:
                integrity = "partial"
            elif provider.get("configured") is False:
                state = "not_admitted"
            elif provider.get("configured") is not True:
                integrity = "partial"
            elif provider.get("requiresCredential") is True:
                credential_configured = provider.get("credentialConfigured")
                if not isinstance(credential_configured, bool):
                    integrity = "partial"
                elif credential_configured:
                    state = (
                        "ready"
                        if self._catalog_market_v3_admission_is_current(key, target, provider)
                        else "not_admitted"
                    )
                else:
                    state = "credential_missing"
            elif provider.get("requiresCredential") is False:
                if self._catalog_market_v3_admission_is_current(key, target, provider):
                    # Ready means an exact, current admission exists for its
                    # recorded scope. Data V3 checks that scope on every request.
                    state = "ready"
                else:
                    state = "not_admitted"
            else:
                integrity = "partial"

            entries.append({"key": key, "target": target, "state": state})

        revision_input = json.dumps(
            {"integrity": integrity, "entries": entries},
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        catalog_revision = int(hashlib.sha256(revision_input).hexdigest()[:13], 16) or 1
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return {
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "catalogRevision": catalog_revision,
            "generatedAt": generated_at,
            "integrity": integrity,
            "entries": entries,
        }

    _catalog_manifest_matches = staticmethod(catalog_manifest_matches_v3)

    def execute_request(self, request: ThesisLedgerDataRequest) -> ProviderExecution:
        """通过当前路由执行请求并返回来源与修订元数据。"""
        if not isinstance(request, ThesisLedgerDataRequest):
            raise TypeError("request 必须是 ThesisLedgerDataRequest")

        capability = request.capability
        instrument_type = request.instrument_type or instrument_type_for_symbol(request.symbol)
        if capability == "REALTIME_QUOTE":
            provider_symbol = provider_symbol_for_contract(request.symbol)

            def operation(provider_id: str, adapter: Any, upstream_source: str | None = None) -> Any:
                value = self._realtime_quote(
                    adapter, provider_id,
                    request.symbol if provider_id == "hithink" else provider_symbol,
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
            provider_symbol = provider_symbol_for_contract(request.symbol)
            days = request.limit or 90
            adjustment = request.adjustment

            def operation(provider_id: str, adapter: Any, upstream_source: str | None = None) -> Any:
                options: dict[str, Any] = {"days": days}
                if adjustment is not None:
                    options["adjustment"] = adjustment
                if request.start is not None:
                    options["start_date"] = request.start
                if request.end is not None:
                    options["end_date"] = request.end
                source_method = getattr(adapter, "get_daily_data_for_source", None)
                unsupported_adjustment = adjustment is not None and provider_id not in {
                    "akshare",
                    "tencent",
                }
                if provider_id == "tencent" and adjustment not in (None, "qfq", "none"):
                    unsupported_adjustment = True
                if provider_id == "tencent" and adjustment == "none":
                    unsupported_adjustment = not (
                        callable(source_method) and upstream_source == "tencent"
                    )
                if unsupported_adjustment:
                    raise ProviderCallError(
                        "unsupported_adjustment",
                        f"{provider_id}/{upstream_source or provider_id} 未证明支持 adjustment={adjustment}",
                    )
                if callable(source_method) and upstream_source:
                    frame = self._daily_frame(
                        source_method(
                            provider_symbol,
                            upstream_source,
                            **options,
                            timeout_seconds=DAILY_BAR_TARGET_TIMEOUT_SECONDS,
                        )
                    )
                elif provider_id == "tencent" and upstream_source == "tencent":
                    frame = self._daily_frame(adapter.get_daily_data(provider_symbol, **options))
                elif provider_id == "efinance" and upstream_source == "eastmoney":
                    frame = self._daily_frame(adapter.get_daily_data(provider_symbol, **options))
                elif upstream_source == provider_id:
                    frame = self._daily_frame(adapter.get_daily_data(provider_symbol, **options))
                elif upstream_source:
                    raise ProviderCallError(
                        "unsupported_source",
                        f"{provider_id} 未提供 source={upstream_source} 的显式适配器",
                    )
                elif adjustment == "none":
                    raw_method = getattr(adapter, "get_daily_data_v2_raw", None)
                    if not callable(raw_method):
                        raise ProviderCallError("unsupported", "Provider 不支持不复权日线")
                    raw_options = {key: value for key, value in options.items() if key != "adjustment"}
                    frame = self._daily_frame(raw_method(provider_symbol, **raw_options))
                else:
                    frame = self._daily_frame(adapter.get_daily_data(provider_symbol, **options))
                attrs = getattr(frame, "attrs", None)
                if isinstance(attrs, dict) and not attrs.get("upstream_source"):
                    if provider_id == "tencent":
                        attrs["upstream_source"] = "tencent"
                    elif provider_id == "efinance":
                        attrs["upstream_source"] = "eastmoney"
                return self._validate_bars(frame)

            return self._execute_request_with_boundary(
                request,
                capability,
                instrument_type,
                operation,
            )

        if capability == "FUND_NAV":
            def latest_nav(provider_id: str, adapter: Any, _upstream_source=None) -> Any:
                frame = self._validate_fund_nav(
                    self._fund_nav_from_provider(provider_id, request.symbol, adapter)
                )
                try:
                    return select_nav_rows(frame, latest_only=True)
                except ValueError as exc:
                    raise ProviderCallError("not_covered", str(exc)) from exc

            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                latest_nav,
            )

        if capability == "FUND_NAV_HISTORY":
            def history_nav(provider_id: str, adapter: Any, _upstream_source=None) -> Any:
                frame = self._validate_fund_nav_history(
                    self._fund_nav_from_provider(provider_id, request.symbol, adapter)
                )
                try:
                    return select_nav_rows(
                        frame, start=request.start, end=request.end, limit=request.limit,
                    )
                except ValueError as exc:
                    raise ProviderCallError("not_covered", str(exc)) from exc

            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                history_nav,
            )

        if capability == "FUND_HOLDINGS":
            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                lambda provider_id, adapter, _upstream_source=None: self._validate_fund_holdings(
                    self._fund_holdings_from_provider(provider_id, request.symbol, adapter)
                ),
            )

        if capability == "CHIP_SUMMARY":
            if instrument_type != "STOCK":
                raise ThesisLedgerGatewayError(
                    "unsupported_capability",
                    "当前筹码合同只支持 STOCK 的 CHIP_SUMMARY",
                    request,
                )
            provider_symbol = provider_symbol_for_contract(request.symbol)

            def operation(_provider_id: str, adapter: Any, _upstream_source: str | None = None) -> Any:
                method = getattr(adapter, "get_chip_distribution", None)
                if method is None:
                    raise ProviderCallError("unsupported", "Provider 不支持筹码摘要")
                return self._validate_chip_summary(method(provider_symbol))

            return self._execute_request_with_boundary(
                request,
                capability,
                instrument_type,
                operation,
            )

        raise ThesisLedgerGatewayError(
            "unsupported_capability",
            f"Capability {capability} 尚未接入 ThesisLedger Provider runtime",
            request,
        )

    def _execute_request_with_boundary(
        self,
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
            effective = self.store.effective_policy_v3()
            targets = select_current_data_targets(
                effective, capability=capability, instrument_type=instrument_type,
                symbol=request.symbol,
            )
            route_key = current_data_route_key(capability, instrument_type, request.symbol)
            market_timezone = {
                "CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York",
            }[route_key["market"]]
            today = datetime.now(ZoneInfo(market_timezone)).date().isoformat()
            date_from = request.start or today
            date_to = request.end or today
            scoped_targets = []
            for provider_id, source, route_index in targets:
                target = {"providerId": provider_id, "upstreamSource": source}
                admission = self._current_market_v3_admission(
                    route_key, target,
                )
                if admission and admission.get("admissionState") == "admitted" and route_admission_scope_applies(
                    admission, symbol=request.symbol, date_from=date_from, date_to=date_to,
                ):
                    scoped_targets.append(RouteTarget(provider_id, source, route_index))
            if not scoped_targets:
                raise CurrentDataRouteError("not_admitted", "当前来源准入未覆盖请求标的或日期")
            result = self._execute_with_metadata(
                capability,
                instrument_type,
                operation,
                budget_symbol=budget_symbol,
                request_id=request_id or request.request_id,
                symbol=request.symbol,
                effective_policy_override=effective,
                route_targets_override=scoped_targets,
            )
            current = self.store.effective_policy_v3()
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
            admission = self._current_market_v3_admission(
                route_key, {"providerId": result.provider,
                            "upstreamSource": result.upstream_source},
            )
            if not admission or not route_admission_scope_applies(
                admission, symbol=request.symbol, date_from=date_from, date_to=date_to,
            ):
                raise CurrentDataRouteError("not_admitted", "数据返回前准入已失效")
            if capability in {"FUND_NAV", "FUND_NAV_HISTORY"}:
                columns = set(getattr(result.value, "columns", []))
                date_column = next(
                    (column for column in ("净值日期", "日期", "date", "nav_date") if column in columns),
                    None,
                )
                if date_column is None:
                    raise CurrentDataRouteError("not_admitted", "基金净值缺少可核对的来源日期")
                returned_dates = [
                    str(row.get(date_column)).strip()[:10]
                    for _, row in result.value.iterrows()
                ]
                if not returned_dates or not route_admission_scope_applies(
                    admission, symbol=request.symbol,
                    date_from=min(returned_dates), date_to=max(returned_dates),
                ):
                    raise CurrentDataRouteError("not_admitted", "基金净值返回日期超出来源准入范围")
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

    @staticmethod
    def _fund_nav_from_provider(provider_id: str, symbol: str, adapter: Any) -> Any:
        """通过 Provider adapter 的兼容方法获取基金净值历史。

        Provider SDK 只允许出现在对应 adapter 内部。runtime 负责能力路由和
        响应校验，不再根据 provider id 直接 import 或调用具体 SDK。
        """
        method = getattr(adapter, "get_fund_nav_history", None)
        if method is None:
            raise ProviderCallError("unsupported", f"{provider_id} 不支持基金净值")
        return method(symbol.removesuffix(".OF"))

    @staticmethod
    def _fund_holdings_from_provider(provider_id: str, symbol: str, adapter: Any) -> Any:
        method = getattr(adapter, "get_fund_holdings", None)
        if method is None:
            raise ProviderCallError("unsupported", f"{provider_id} 不支持基金持仓披露")
        return method(symbol.removesuffix(".OF"))

    def smoke(
        self,
        provider_id: str,
        capability: str,
        *,
        credential_snapshot: ProviderCredentialSnapshot | None = None,
        draft_probe: bool | None = None,
    ) -> dict[str, Any]:
        """Run one bounded, read-only representative call without changing policy."""
        normalized_capability = capability.strip().upper()
        instrument_type = (
            "MUTUAL_FUND"
            if normalized_capability in {"FUND_NAV", "FUND_NAV_HISTORY", "FUND_HOLDINGS"}
            else "STOCK"
        )
        started = self.clock()
        if draft_probe is None:
            draft_probe = credential_snapshot is not None
        circuit_key = f"thesis-ledger:{provider_id}:{normalized_capability}:{instrument_type}"
        manifest = PROVIDER_MANIFESTS.get(provider_id, {})
        sample_contract_symbol = (
            "AAPL.US" if "US" in manifest.get("markets", ()) else "600519.SH"
        )
        sample_symbol = provider_symbol_for_contract(sample_contract_symbol)

        def record_failure(code: str) -> None:
            if draft_probe:
                return
            self.store.record_health(
                provider_id,
                normalized_capability,
                instrument_type,
                state="degraded",
                circuit=self.circuit.state(circuit_key, self.clock()),
                error_code=code,
            )

        try:
            adapter = self._adapter(
                provider_id,
                credential_snapshot,
                cache=False,
                probe=True,
            )
            if normalized_capability == "REALTIME_QUOTE":
                value = self._realtime_quote(
                    adapter,
                    provider_id,
                    sample_symbol,
                )
                if value is None or getattr(value, "price", None) is None:
                    raise ProviderCallError("not_covered", "Provider 未返回代表性行情")
            elif normalized_capability == "DAILY_BAR":
                if provider_id in {"finnhub", "alphavantage"}:
                    daily_data = adapter.get_daily_data(sample_symbol, days=5, strict=True)
                else:
                    daily_data = adapter.get_daily_data(sample_symbol, days=5)
                frame = self._daily_frame(daily_data)
                self._validate_bars(frame)
            elif normalized_capability == "FUND_NAV":
                value = self._fund_nav_from_provider(provider_id, "000001.OF", adapter)
                self._validate_fund_nav(value)
            elif normalized_capability == "FUND_NAV_HISTORY":
                value = self._fund_nav_from_provider(provider_id, "000001.OF", adapter)
                self._validate_fund_nav_history(value)
            elif normalized_capability == "FUND_HOLDINGS":
                value = self._fund_holdings_from_provider(provider_id, "000001.OF", adapter)
                self._validate_fund_holdings(value)
            elif normalized_capability == "CHIP_SUMMARY":
                method = getattr(adapter, "get_chip_distribution", None)
                if method is None:
                    raise ProviderCallError("unsupported", "Provider 不支持筹码摘要")
                self._validate_chip_summary(method(sample_symbol))
            else:
                raise ProviderCallError("unsupported", "Provider 不支持该 Capability")
        except ProviderCallError as exc:
            record_failure(exc.code)
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            code = classify_provider_exception(exc)
            record_failure(code)
            raise ProviderCallError(code, "Provider 暂时不可用", retryable=True) from exc
        except Exception as exc:  # adapter boundary; never expose raw smoke errors.
            code = classify_provider_exception(exc)
            logger.warning(
                "ThesisLedger Provider smoke failure provider=%s capability=%s code=%s",
                provider_id,
                normalized_capability,
                code,
            )
            record_failure(code)
            raise ProviderCallError(code, "Provider smoke 调用失败") from exc
        elapsed_ms = max(0, int((self.clock() - started) * 1000))
        if not draft_probe:
            self.circuit.success(circuit_key)
            self.store.record_health(
                provider_id,
                normalized_capability,
                instrument_type,
                state="healthy",
                circuit="closed",
                consecutive_failures=0,
                latency_ms=elapsed_ms,
            )
        return {
            "status": "healthy",
            "attempted": True,
            "readOnly": True,
            "latencyMs": elapsed_ms,
        }


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
