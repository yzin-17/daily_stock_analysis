# -*- coding: utf-8 -*-
"""ThesisLedger consumer 使用的 Provider-neutral 标的目录构建器。"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

_CATALOG_PROVIDER_PROCESS_START_METHOD = "spawn"
_CATALOG_PROVIDER_PROCESS_JOIN_GRACE_SECONDS = 1.0
_CATALOG_PROVIDER_WORKER_SLOTS = threading.BoundedSemaphore(2)
_CATALOG_PROVIDER_MAX_ATTEMPTS = 2
_CATALOG_PROVIDER_RETRY_BACKOFF_SECONDS = 0.25

ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")


class CatalogBuildError(RuntimeError):
    """所有目录 Provider 均失败。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "catalog_build_failed",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def _terminate_catalog_provider_process(process: Any) -> None:
    """终止并回收 Provider 子进程，必要时升级为 kill。"""

    if process is None:
        return
    try:
        if process.is_alive():
            process.terminate()
            process.join(_CATALOG_PROVIDER_PROCESS_JOIN_GRACE_SECONDS)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(_CATALOG_PROVIDER_PROCESS_JOIN_GRACE_SECONDS)
        elif process.pid is not None:
            process.join(_CATALOG_PROVIDER_PROCESS_JOIN_GRACE_SECONDS)
    except (OSError, AttributeError):
        logger.debug("Catalog Provider process cleanup failed", exc_info=True)


def _catalog_provider_process_worker(connection: Any, operation: Callable[[], Any]) -> None:
    """在隔离进程中执行一个 Provider callable，只回传可序列化结果。"""

    try:
        connection.send((True, operation()))
    except BaseException as exc:
        try:
            connection.send((False, type(exc).__name__, str(exc)[:256]))
        except BaseException:
            # Provider 可能在发送错误前退出；父进程会按稳定的 isolation code 处理。
            pass
    finally:
        connection.close()


def _run_catalog_provider_once(
    operation: Callable[[], Any],
    *,
    timeout_seconds: float,
) -> Any:
    wait_seconds = max(0.01, float(timeout_seconds))
    if not _CATALOG_PROVIDER_WORKER_SLOTS.acquire(blocking=False):
        raise CatalogBuildError(
            "Catalog Provider 并发已达到上限",
            code="catalog_provider_concurrency_limited",
        )

    process: Any = None
    process_started = False
    parent_connection: Any = None
    child_connection: Any = None
    try:
        multiprocessing.freeze_support()
        context = multiprocessing.get_context(_CATALOG_PROVIDER_PROCESS_START_METHOD)
        parent_connection, child_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_catalog_provider_process_worker,
            args=(child_connection, operation),
            name="catalog-provider",
            daemon=True,
        )
        process.start()
        process_started = True
        child_connection.close()
        child_connection = None

        if not parent_connection.poll(wait_seconds):
            if process.is_alive():
                raise CatalogBuildError(
                    "Provider 目录请求超时，已终止隔离进程",
                    code="catalog_provider_timeout",
                )
            raise CatalogBuildError(
                "Provider 隔离进程未返回结果",
                code="catalog_provider_no_result",
            )
        try:
            result = parent_connection.recv()
        except EOFError as exc:
            raise CatalogBuildError(
                "Provider 隔离进程未返回结果",
                code="catalog_provider_no_result",
            ) from exc
        if result[0]:
            return result[1]
        raise CatalogBuildError(
            "Provider 目录请求失败",
            code="catalog_provider_unavailable",
        )
    except CatalogBuildError:
        raise
    except Exception as exc:
        # 例如 callable 无法被 spawn pickle，不能退回到不可终止的线程执行。
        raise CatalogBuildError(
            "Provider 隔离进程启动失败",
            code="catalog_provider_isolation_failed",
            retryable=False,
        ) from exc
    finally:
        if child_connection is not None:
            child_connection.close()
        if parent_connection is not None:
            parent_connection.close()
        if process_started:
            _terminate_catalog_provider_process(process)
        _CATALOG_PROVIDER_WORKER_SLOTS.release()


def _bounded_call(
    operation: Callable[[], Any],
    timeout_seconds: float = 60.0,
    *,
    max_attempts: int = _CATALOG_PROVIDER_MAX_ATTEMPTS,
    backoff_seconds: float = _CATALOG_PROVIDER_RETRY_BACKOFF_SECONDS,
) -> Any:
    """在可终止子进程中执行 Provider，并限制重试和退避。"""

    attempts = max(1, int(max_attempts))
    last_error: CatalogBuildError | None = None
    for attempt in range(attempts):
        try:
            return _run_catalog_provider_once(
                operation,
                timeout_seconds=timeout_seconds,
            )
        except CatalogBuildError as exc:
            last_error = exc
            if not exc.retryable or attempt >= attempts - 1:
                raise
            time.sleep(min(max(0.0, float(backoff_seconds)) * (2**attempt), 2.0))
    assert last_error is not None
    raise last_error


def _column(frame: Any, candidates: Iterable[str]) -> str | None:
    columns = set(getattr(frame, "columns", []))
    return next((candidate for candidate in candidates if candidate in columns), None)


def _market_for(code: str) -> str:
    if code.startswith(("4", "8")):
        return "BJ"
    if code.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _normalize_frame(
    frame: Any,
    *,
    instrument_type: str | None = None,
    market: str | None = None,
) -> list[dict[str, str]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    code_column = _column(frame, ("code", "代码", "股票代码", "基金代码"))
    name_column = _column(frame, ("name", "名称", "股票名称", "基金简称", "基金名称"))
    if code_column is None or name_column is None:
        raise CatalogBuildError(
            "Provider 目录响应缺少代码或名称字段",
            code="catalog_provider_invalid_response",
            retryable=False,
        )
    items: list[dict[str, str]] = []
    for _, row in frame.iterrows():
        code = str(row.get(code_column) or "").strip().upper().zfill(6)
        name = str(row.get(name_column) or "").strip()
        if len(code) != 6 or not code.isdigit() or not name:
            continue
        resolved_type = instrument_type or ("ETF" if code.startswith(ETF_PREFIXES) else "STOCK")
        resolved_market = market or _market_for(code)
        items.append(
            {
                "canonicalCode": code,
                "instrumentType": resolved_type,
                "market": resolved_market,
                "displayName": name,
            }
        )
    return items


def _akshare_catalog() -> list[dict[str, str]]:
    import akshare as ak

    items: list[dict[str, str]] = []
    items.extend(_normalize_frame(ak.stock_info_a_code_name()))
    items.extend(_normalize_frame(ak.fund_etf_spot_em(), instrument_type="ETF"))
    items.extend(
        _normalize_frame(
            ak.fund_name_em(),
            instrument_type="MUTUAL_FUND",
            market="OF",
        )
    )
    return items


def _efinance_catalog() -> list[dict[str, str]]:
    import efinance as ef

    items: list[dict[str, str]] = []
    stock = getattr(ef, "stock", None)
    stock_quotes = getattr(stock, "get_realtime_quotes", None)
    if callable(stock_quotes):
        items.extend(_normalize_frame(stock_quotes()))
    fund = getattr(ef, "fund", None)
    fund_quotes = getattr(fund, "get_realtime_quotes", None)
    if callable(fund_quotes):
        items.extend(
            _normalize_frame(
                fund_quotes(),
                instrument_type="MUTUAL_FUND",
                market="OF",
            )
        )
    if not items:
        raise CatalogBuildError("efinance 不提供可用目录接口")
    return items


PROVIDER_CATALOG_LOADERS: dict[str, Callable[[], list[dict[str, str]]]] = {
    "akshare": _akshare_catalog,
    "efinance": _efinance_catalog,
}


def build_catalog(
    loaders: dict[str, Callable[[], list[dict[str, str]]]] | None = None,
    *,
    provider_timeout_seconds: float = 60.0,
    max_attempts: int = _CATALOG_PROVIDER_MAX_ATTEMPTS,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """抓取并合并目录；至少一个 Provider 成功才生成新 snapshot。"""

    merged: dict[tuple[str, str, str], dict[str, str]] = {}
    failures: dict[str, str] = {}
    for provider_id, loader in (loaders or PROVIDER_CATALOG_LOADERS).items():
        try:
            provider_items = _bounded_call(
                loader,
                timeout_seconds=provider_timeout_seconds,
                max_attempts=max_attempts,
            )
            if not provider_items:
                raise CatalogBuildError(
                    "Provider 返回空目录",
                    code="catalog_provider_empty",
                    retryable=False,
                )
            for item in provider_items:
                key = (
                    item["canonicalCode"],
                    item["market"],
                    item["instrumentType"],
                )
                merged.setdefault(key, item)
        except Exception as exc:  # Provider 原始错误只写日志和稳定诊断。
            logger.warning("Catalog provider failed provider=%s: %s", provider_id, exc)
            failures[provider_id] = getattr(
                exc,
                "code",
                "catalog_provider_unavailable",
            )
    if not merged:
        raise CatalogBuildError(
            "所有 Catalog Provider 均不可用",
            code="catalog_all_providers_unavailable",
        )
    return [merged[key] for key in sorted(merged)], failures
