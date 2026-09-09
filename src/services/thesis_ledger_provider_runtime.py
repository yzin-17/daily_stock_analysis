# -*- coding: utf-8 -*-
"""DSA ThesisLedger consumer namespace 的 Provider 路由执行器。

它只按 DSA SQLite 中的 Effective Policy 顺序执行。旧的
``DataFetcherManager`` 仍服务 DSA native analysis；本模块不把 native 的默认
优先级或隐藏 fallback 泄漏到 ThesisLedger consumer。
"""

from __future__ import annotations

import importlib
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Mapping

from src.services.thesis_ledger_control import ThesisLedgerControlStore

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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.request_id = request_id
        self.diagnostic_id = diagnostic_id or request_id

    def detail(self, *, contract_version: int = 1) -> dict[str, Any]:
        """Return a stable error projection without exposing upstream details."""
        request_id = self.request_id or str(uuid.uuid4())
        return {
            "contractVersion": contract_version,
            "code": self.code,
            "message": str(self),
            "requestId": request_id,
            "diagnosticId": self.diagnostic_id or request_id,
        }


@dataclass(frozen=True)
class ThesisLedgerDataRequest:
    """Standard input shared by every ThesisLedger consumer capability.

    The request intentionally carries optional range parameters even when a
    current runtime operation does not need all of them.  This gives later
    capability migrations one stable boundary without changing Data Contract
    endpoint behavior in the compatibility expansion.
    """

    capability: str
    symbol: str
    timeframe: str | None = None
    start: str | None = None
    end: str | None = None
    limit: int | None = None
    instrument_type: str | None = None
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
        object.__setattr__(self, "parameters", dict(self.parameters))
        object.__setattr__(
            self,
            "request_id",
            str(self.request_id or uuid.uuid4()).strip() or str(uuid.uuid4()),
        )


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


class ThesisLedgerGatewayError(ProviderCallError):
    """Stable error carrying the request identity at the gateway boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        request: ThesisLedgerDataRequest,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(
            code,
            message,
            retryable=retryable,
            request_id=request.request_id,
            diagnostic_id=request.request_id,
        )
        self.request = request

    def detail(self, *, contract_version: int = 1) -> dict[str, Any]:
        """Return a request-correlated stable Contract error projection."""
        return super().detail(contract_version=contract_version)


def validate_fund_nav_history_rows(rows: list[tuple[Any, Any]]) -> None:
    """校验基金净值历史的非空、日期唯一升序和正数净值约束。"""
    if not rows:
        raise ProviderCallError("not_covered", "Provider 未返回基金净值历史")
    seen: set[str] = set()
    previous: datetime | None = None
    for date_value, nav_value in rows:
        date_text = str(date_value or "").strip()
        if not date_text or date_text in seen:
            raise ProviderCallError("invalid_response", "Provider 净值日期缺失或重复")
        seen.add(date_text)
        try:
            current = datetime.fromisoformat(date_text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProviderCallError("invalid_response", "Provider 净值日期格式非法") from exc
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if previous is not None and current <= previous:
            raise ProviderCallError("invalid_response", "Provider 净值历史必须严格升序")
        previous = current
        try:
            nav = float(nav_value)
        except (TypeError, ValueError) as exc:
            raise ProviderCallError("invalid_response", "Provider 响应字段 unitNav 非法") from exc
        if not math.isfinite(nav) or nav <= 0:
            raise ProviderCallError("invalid_response", "Provider 响应字段 unitNav 非法")


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
        self.clock = clock or time.monotonic
        self.circuit = _ScopedCircuit()

    def _adapter(self, provider_id: str) -> Any:
        if provider_id in self.adapters:
            return self.adapters[provider_id]
        try:
            adapter_import = _PROVIDER_ADAPTER_IMPORTS.get(provider_id)
            if adapter_import is None:
                raise ProviderCallError("UNKNOWN_PROVIDER", "未知 Provider")
            module_name, class_name = adapter_import
            adapter_class = getattr(importlib.import_module(module_name), class_name)
            if provider_id == "tickflow":
                from src.config import get_config

                config = get_config()
                adapter = adapter_class(
                    api_key=getattr(config, "tickflow_api_key", None),
                    kline_adjust=getattr(config, "tickflow_kline_adjust", "none"),
                    batch_daily_enabled=getattr(config, "tickflow_batch_daily_enabled", True),
                    batch_size=getattr(config, "tickflow_batch_size", 100),
                    priority=getattr(config, "tickflow_priority", 2),
                )
            else:
                adapter = adapter_class()
        except ProviderCallError:
            raise
        except Exception as exc:  # optional adapter dependency/configuration.
            raise ProviderCallError("not_configured", "Provider 适配器未就绪") from exc
        self.adapters[provider_id] = adapter
        return adapter

    @staticmethod
    def _daily_frame(adapter_result: Any) -> Any:
        """兼容 BaseFetcher 的 DataFrame 与 manager 的 ``(frame, source)`` 返回值。"""
        if isinstance(adapter_result, tuple):
            if len(adapter_result) != 2:
                raise ProviderCallError("invalid_response", "Provider 日线响应结构非法")
            return adapter_result[0]
        return adapter_result

    @staticmethod
    def _realtime_quote(adapter: Any, provider_id: str, symbol: str) -> Any:
        """为 AKShare 选择单标的轻量通道，其他 Provider 使用统一入口。"""
        if provider_id == "akshare":
            value = adapter.get_realtime_quote(symbol, source="sina")
        else:
            value = adapter.get_realtime_quote(symbol)
        if isinstance(value, Mapping):
            return SimpleNamespace(
                price=value.get("price"),
                open_price=value.get("open_price", value.get("open")),
                high=value.get("high"),
                low=value.get("low"),
                pre_close=value.get("pre_close", value.get("previousClose")),
                volume=value.get("volume"),
                amount=value.get("amount"),
                change_amount=value.get("change_amount"),
                change_pct=value.get("change_pct"),
                fetched_at=value.get("fetched_at"),
                provider_timestamp=value.get("provider_timestamp"),
                is_stale=value.get("is_stale", False),
                source=value.get("source"),
            )
        return value

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
            date_value = str(row.get(date_column) or "").strip()
            if not date_value or date_value in dates:
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
        required = {"股票代码", "股票名称", "占净值比例", "季度"}
        if not required.issubset(set(getattr(frame, "columns", []))):
            raise ProviderCallError("invalid_response", "Provider 基金持仓响应缺少必要字段")
        for _, row in frame.iterrows():
            symbol = str(row.get("股票代码") or "").strip()
            quarter = str(row.get("季度") or "").strip()
            if not symbol or not quarter:
                raise ProviderCallError("invalid_response", "Provider 基金持仓代码或报告期缺失")
            weight = cls._finite_number(row.get("占净值比例"), "weight")
            if weight > 100:
                raise ProviderCallError("invalid_response", "Provider 基金持仓权重超出范围")
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

    @staticmethod
    def _providers_from_effective(
        effective_policy: Mapping[str, Any] | None,
        capability: str,
        instrument_type: str,
    ) -> list[str]:
        """Read one route from the same Effective Policy snapshot as metadata."""
        if not effective_policy or not effective_policy.get("enabled"):
            return []
        status = (
            effective_policy.get("routeStatus", {})
            .get(capability.upper(), {})
            .get(instrument_type.upper(), {})
        )
        return [
            str(entry["providerId"])
            for entry in status.get("providers", [])
            if entry.get("eligible")
        ]

    def _execute_with_metadata(
        self,
        capability: str,
        instrument_type: str,
        operation: Callable[[str, Any], Any],
    ) -> ProviderExecution:
        effective_policy = self.store.effective_policy()
        providers = self._providers_from_effective(
            effective_policy,
            capability,
            instrument_type,
        )
        if not providers:
            raise NoEligibleProviderError(capability, instrument_type)
        fallback_used = False
        attempted_providers: list[str] = []
        last_error: ProviderCallError | None = None
        for index, provider_id in enumerate(providers):
            if index > 0:
                fallback_used = True
            key = f"thesis-ledger:{provider_id}:{capability}:{instrument_type}"
            now = self.clock()
            persisted_health = self.store.health(provider_id, capability, instrument_type)
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
                continue
            attempts = 2
            for attempt in range(attempts):
                started = self.clock()
                try:
                    if provider_id not in attempted_providers:
                        attempted_providers.append(provider_id)
                    result = operation(provider_id, self._adapter(provider_id))
                    if result is None:
                        raise ProviderCallError("not_covered", "Provider 未覆盖该标的")
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
                    )
                    return ProviderExecution(
                        value=result,
                        capability=capability,
                        instrument_type=instrument_type,
                        provider=provider_id,
                        fallback_used=fallback_used,
                        effective_policy=effective_policy,
                        route=tuple(providers),
                        attempted_providers=tuple(attempted_providers),
                    )
                except ProviderCallError as exc:
                    last_error = exc
                except (TimeoutError, ConnectionError, OSError) as exc:
                    last_error = ProviderCallError(
                        "transient_failure", "Provider 暂时不可用", retryable=True
                    )
                    logger.warning("ThesisLedger Provider transient failure: %s", exc)
                except Exception as exc:  # adapter boundary; do not expose raw error.
                    last_error = ProviderCallError("upstream_failure", "Provider 请求失败")
                    logger.warning(
                        "ThesisLedger Provider failure provider=%s capability=%s: %s",
                        provider_id,
                        capability,
                        exc,
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
                )
        if last_error is None:
            raise ProviderCallError("upstream_unavailable", "Provider 暂时不可用")
        if last_error.code == "circuit_open" and not attempted_providers:
            raise NoEligibleProviderError(capability, instrument_type)
        if last_error.code in {"not_covered", "unsupported", "circuit_open"}:
            raise ProviderCallError("upstream_unavailable", "没有 Provider 返回可用数据")
        raise last_error

    def _execute(
        self,
        capability: str,
        instrument_type: str,
        operation: Callable[[str, Any], Any],
    ) -> tuple[Any, str, bool]:
        """Keep the tuple return shape used by the existing Data Contract facade."""
        execution = self._execute_with_metadata(capability, instrument_type, operation)
        return execution.value, execution.provider, execution.fallback_used

    def quote(self, symbol: str) -> tuple[Any, str, bool]:
        """Keep the legacy tuple API while delegating execution to the gateway boundary."""
        execution = self.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", symbol))
        return execution.value, execution.provider, execution.fallback_used

    def bars(self, symbol: str, days: int = 90) -> tuple[Any, str, bool]:
        """Keep the legacy tuple API while delegating execution to the gateway boundary."""
        execution = self.execute_request(
            ThesisLedgerDataRequest("DAILY_BAR", symbol, limit=days)
        )
        return execution.value, execution.provider, execution.fallback_used

    def fund_nav(self, symbol: str) -> tuple[Any, str, bool]:
        """Keep the legacy tuple API while delegating execution to the gateway boundary."""
        execution = self.execute_request(ThesisLedgerDataRequest("FUND_NAV", symbol))
        return execution.value, execution.provider, execution.fallback_used

    def fund_nav_history(self, symbol: str) -> tuple[Any, str, bool]:
        """Keep the legacy tuple API while delegating execution to the gateway boundary."""
        execution = self.execute_request(ThesisLedgerDataRequest("FUND_NAV_HISTORY", symbol))
        return execution.value, execution.provider, execution.fallback_used

    def fund_holdings(self, symbol: str) -> tuple[Any, str, bool]:
        """通过统一路由获取基金披露持仓。"""
        execution = self.execute_request(ThesisLedgerDataRequest("FUND_HOLDINGS", symbol))
        return execution.value, execution.provider, execution.fallback_used

    def chip_summary(self, symbol: str) -> tuple[Any, str, bool]:
        """Keep a tuple convenience API for the explicit CHIP_SUMMARY route."""
        execution = self.execute_request(ThesisLedgerDataRequest("CHIP_SUMMARY", symbol))
        return execution.value, execution.provider, execution.fallback_used

    def execute_request(self, request: ThesisLedgerDataRequest) -> ProviderExecution:
        """Execute a standard request for one provider-routed capability.

        This is the compatibility expansion entry point. Existing tuple
        methods remain unchanged for current facade callers; derived consumers
        can consume the metadata-rich ``ProviderExecution`` directly.
        """
        if not isinstance(request, ThesisLedgerDataRequest):
            raise TypeError("request 必须是 ThesisLedgerDataRequest")

        capability = request.capability
        instrument_type = request.instrument_type or instrument_type_for_symbol(request.symbol)
        if capability == "REALTIME_QUOTE":
            provider_symbol = provider_symbol_for_contract(request.symbol)

            def operation(provider_id: str, adapter: Any) -> Any:
                value = self._realtime_quote(adapter, provider_id, provider_symbol)
                if value is None:
                    raise ProviderCallError("not_covered", "Provider 未覆盖该标的")
                return self._validate_quote(value)

            return self._execute_request_with_boundary(
                request,
                capability,
                instrument_type,
                operation,
            )

        if capability == "DAILY_BAR":
            if request.timeframe not in (None, "1d"):
                raise ThesisLedgerGatewayError(
                    "unsupported_capability",
                    "Contract V1 只支持 1d bars",
                    request,
                )
            provider_symbol = provider_symbol_for_contract(request.symbol)
            days = request.limit or 90
            raw_mode = str(request.parameters.get("priceMode") or "").strip().lower() == "raw"

            def operation(provider_id: str, adapter: Any) -> Any:
                options: dict[str, Any] = {"days": days}
                if request.start is not None:
                    options["start_date"] = request.start
                if request.end is not None:
                    options["end_date"] = request.end
                if raw_mode:
                    raw_method = getattr(adapter, "get_daily_data_v2_raw", None)
                    if not callable(raw_method):
                        raise ProviderCallError("unsupported", "Provider 不支持 V2 不复权日线")
                    frame = self._daily_frame(raw_method(provider_symbol, **options))
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
            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                lambda provider_id, adapter: self._validate_fund_nav(
                    self._fund_nav_from_provider(provider_id, request.symbol, adapter)
                ),
            )

        if capability == "FUND_NAV_HISTORY":
            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                lambda provider_id, adapter: self._validate_fund_nav_history(
                    self._fund_nav_from_provider(provider_id, request.symbol, adapter)
                ),
            )

        if capability == "FUND_HOLDINGS":
            return self._execute_request_with_boundary(
                request,
                capability,
                "MUTUAL_FUND",
                lambda provider_id, adapter: self._validate_fund_holdings(
                    self._fund_holdings_from_provider(provider_id, request.symbol, adapter)
                ),
            )

        if capability == "CHIP_SUMMARY":
            if instrument_type != "STOCK":
                raise ThesisLedgerGatewayError(
                    "unsupported_capability",
                    "Contract V1 只支持 STOCK 的 CHIP_SUMMARY",
                    request,
                )
            provider_symbol = provider_symbol_for_contract(request.symbol)

            def operation(_provider_id: str, adapter: Any) -> Any:
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
    ) -> ProviderExecution:
        """Attach request identity while preserving stable runtime error codes."""
        try:
            return self._execute_with_metadata(capability, instrument_type, operation)
        except ThesisLedgerGatewayError:
            raise
        except ProviderCallError as exc:
            raise ThesisLedgerGatewayError(
                exc.code,
                str(exc),
                request,
                retryable=exc.retryable,
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

    def smoke(self, provider_id: str, capability: str) -> dict[str, Any]:
        """Run one bounded, read-only representative call without changing policy."""
        normalized_capability = capability.strip().upper()
        instrument_type = (
            "MUTUAL_FUND"
            if normalized_capability in {"FUND_NAV", "FUND_NAV_HISTORY", "FUND_HOLDINGS"}
            else "STOCK"
        )
        started = self.clock()
        circuit_key = f"thesis-ledger:{provider_id}:{normalized_capability}:{instrument_type}"

        def record_failure(code: str) -> None:
            self.store.record_health(
                provider_id,
                normalized_capability,
                instrument_type,
                state="degraded",
                circuit=self.circuit.state(circuit_key, self.clock()),
                error_code=code,
            )

        try:
            adapter = self._adapter(provider_id)
            if normalized_capability == "REALTIME_QUOTE":
                value = self._realtime_quote(
                    adapter,
                    provider_id,
                    provider_symbol_for_contract("600519.SH"),
                )
                if value is None or getattr(value, "price", None) is None:
                    raise ProviderCallError("not_covered", "Provider 未返回代表性行情")
            elif normalized_capability == "DAILY_BAR":
                frame = self._daily_frame(
                    adapter.get_daily_data(
                        provider_symbol_for_contract("600519.SH"), days=5
                    )
                )
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
                self._validate_chip_summary(method("600519"))
            else:
                raise ProviderCallError("unsupported", "Provider 不支持该 Capability")
        except ProviderCallError as exc:
            record_failure(exc.code)
            raise
        except (TimeoutError, ConnectionError, OSError) as exc:
            record_failure("transient_failure")
            raise ProviderCallError("transient_failure", "Provider 暂时不可用", retryable=True) from exc
        except Exception as exc:  # adapter boundary; never expose raw smoke errors.
            logger.warning(
                "ThesisLedger Provider smoke failure provider=%s capability=%s: %s",
                provider_id,
                normalized_capability,
                exc,
            )
            record_failure("upstream_failure")
            raise ProviderCallError("upstream_failure", "Provider smoke 调用失败") from exc
        elapsed_ms = max(0, int((self.clock() - started) * 1000))
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
    """Standard output shared by ThesisLedger consumer capability callers."""

    request: ThesisLedgerDataRequest
    data: Any
    provider: str
    fallback_used: bool
    effective_policy: Mapping[str, Any] | None
    route: tuple[str, ...]
    attempted_providers: tuple[str, ...]
    served_from_cache: bool = False

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
    def fallbackUsed(self) -> bool:  # noqa: N802 - Contract-compatible alias.
        """Expose the existing Data Contract spelling for compatibility."""
        return self.fallback_used

    @property
    def servedFromCache(self) -> bool:  # noqa: N802 - Contract-compatible alias.
        """Expose cache provenance separately from the actual Provider."""
        return self.served_from_cache

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
    """Single compatibility boundary for ThesisLedger consumer data access.

    Core provider-routed capabilities are delegated to
    :class:`ThesisLedgerProviderRuntime`.  Optional handlers make the boundary
    extensible for derived capabilities.  Handlers must return a
    ``ProviderExecution`` so provenance cannot be silently discarded.
    """

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
