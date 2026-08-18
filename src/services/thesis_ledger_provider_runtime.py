# -*- coding: utf-8 -*-
"""DSA ThesisLedger consumer namespace 的 Provider 路由执行器。

它只按 DSA SQLite 中的 Effective Policy 顺序执行。旧的
``DataFetcherManager`` 仍服务 DSA native analysis；本模块不把 native 的默认
优先级或隐藏 fallback 泄漏到 ThesisLedger consumer。
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from src.services.thesis_ledger_control import ThesisLedgerControlStore

logger = logging.getLogger(__name__)


class ProviderCallError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class NoEligibleProviderError(ProviderCallError):
    def __init__(self, capability: str, instrument_type: str) -> None:
        super().__init__(
            "NO_ELIGIBLE_PROVIDER",
            f"没有可执行的 {capability}/{instrument_type} Provider",
        )


def instrument_type_for_symbol(symbol: str) -> str:
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
    def __init__(self) -> None:
        self._states: dict[str, _CircuitState] = {}
        self._lock = threading.RLock()

    def allow(self, key: str, now: float) -> bool:
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
        with self._lock:
            self._states[key] = _CircuitState()

    def hydrate_open(self, key: str, now: float, remaining_seconds: float) -> None:
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.failures = max(state.failures, 3)
            state.open_until = max(state.open_until, now + max(0.0, remaining_seconds))
            state.half_open = False

    def failure(self, key: str, now: float) -> tuple[str, int]:
        with self._lock:
            state = self._states.setdefault(key, _CircuitState())
            state.failures += 1
            if state.failures >= 3:
                state.open_until = now + 60
                state.half_open = False
                return "open", state.failures
            return "closed", state.failures

    def state(self, key: str, now: float) -> str:
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
            if provider_id == "akshare":
                from data_provider.akshare_fetcher import AkshareFetcher

                adapter = AkshareFetcher()
            elif provider_id == "efinance":
                from data_provider.efinance_fetcher import EfinanceFetcher

                adapter = EfinanceFetcher()
            else:
                raise ProviderCallError("UNKNOWN_PROVIDER", "未知 Provider")
        except ProviderCallError:
            raise
        except Exception as exc:  # optional adapter dependency/configuration.
            raise ProviderCallError("not_configured", "Provider 适配器未就绪") from exc
        self.adapters[provider_id] = adapter
        return adapter

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
        fields = {
            "open": getattr(value, "open_price", None),
            "high": getattr(value, "high", None),
            "low": getattr(value, "low", None),
            "price": getattr(value, "price", None),
            "previousClose": getattr(value, "pre_close", None),
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

    def _execute(
        self,
        capability: str,
        instrument_type: str,
        operation: Callable[[str, Any], Any],
    ) -> tuple[Any, str, bool]:
        providers = self.store.route(
            capability,
            instrument_type,
            include_circuit_open=True,
        )
        if not providers:
            raise NoEligibleProviderError(capability, instrument_type)
        fallback_used = False
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
                    return result, provider_id, fallback_used
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
        if last_error.code in {"not_covered", "unsupported", "circuit_open"}:
            raise ProviderCallError("upstream_unavailable", "没有 Provider 返回可用数据")
        raise last_error

    def quote(self, symbol: str) -> tuple[Any, str, bool]:
        normalized = symbol.strip().upper()

        def operation(_provider_id: str, adapter: Any) -> Any:
            value = adapter.get_realtime_quote(normalized)
            if value is None:
                raise ProviderCallError("not_covered", "Provider 未覆盖该标的")
            return self._validate_quote(value)

        return self._execute("REALTIME_QUOTE", instrument_type_for_symbol(normalized), operation)

    def bars(self, symbol: str, days: int = 90) -> tuple[Any, str, bool]:
        normalized = symbol.strip().upper()

        def operation(_provider_id: str, adapter: Any) -> Any:
            frame, source = adapter.get_daily_data(normalized, days=days)
            return self._validate_bars(frame), source

        return self._execute("DAILY_BAR", instrument_type_for_symbol(normalized), operation)

    def fund_nav(self, symbol: str) -> tuple[Any, str, bool]:
        normalized = symbol.strip().upper()
        return self._execute(
            "FUND_NAV",
            "MUTUAL_FUND",
            lambda provider_id, adapter: self._validate_fund_nav(
                self._fund_nav_from_provider(provider_id, normalized, adapter)
            ),
        )

    def fund_nav_history(self, symbol: str) -> tuple[Any, str, bool]:
        normalized = symbol.strip().upper()
        return self._execute(
            "FUND_NAV_HISTORY",
            "MUTUAL_FUND",
            lambda provider_id, adapter: self._validate_fund_nav(
                self._fund_nav_from_provider(provider_id, normalized, adapter)
            ),
        )

    @staticmethod
    def _fund_nav_from_provider(provider_id: str, symbol: str, _adapter: Any) -> Any:
        code = symbol.removesuffix(".OF")
        if provider_id == "akshare":
            import akshare as ak

            return ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
        if provider_id == "efinance":
            import efinance as ef

            fund = getattr(ef, "fund", None)
            method = getattr(fund, "get_quote_history", None)
            if method is None:
                raise ProviderCallError("unsupported", "efinance 不支持基金净值")
            return method(code)
        raise ProviderCallError("unsupported", "Provider 不支持基金净值")

    def smoke(self, provider_id: str, capability: str) -> dict[str, Any]:
        """Run one bounded, read-only representative call without changing policy."""
        normalized_capability = capability.strip().upper()
        instrument_type = (
            "MUTUAL_FUND"
            if normalized_capability in {"FUND_NAV", "FUND_NAV_HISTORY"}
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
                value = adapter.get_realtime_quote("600519.SH")
                if value is None or getattr(value, "price", None) is None:
                    raise ProviderCallError("not_covered", "Provider 未返回代表性行情")
            elif normalized_capability == "DAILY_BAR":
                frame, _source = adapter.get_daily_data("600519.SH", days=5)
                if frame is None or getattr(frame, "empty", True):
                    raise ProviderCallError("not_covered", "Provider 未返回代表性日线")
            elif normalized_capability in {"FUND_NAV", "FUND_NAV_HISTORY"}:
                value = self._fund_nav_from_provider(provider_id, "000001.OF", adapter)
                if value is None or getattr(value, "empty", True):
                    raise ProviderCallError("not_covered", "Provider 未返回代表性基金净值")
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


_runtime: ThesisLedgerProviderRuntime | None = None
_runtime_database_path: str | None = None


def get_thesis_ledger_runtime() -> ThesisLedgerProviderRuntime:
    global _runtime, _runtime_database_path
    database_path = os.getenv("DATABASE_PATH", "./data/stock_analysis.db").strip() or "./data/stock_analysis.db"
    if _runtime is None or _runtime_database_path != database_path:
        _runtime = ThesisLedgerProviderRuntime()
        _runtime_database_path = database_path
    return _runtime
