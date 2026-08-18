# -*- coding: utf-8 -*-
"""ThesisLedger consumer 使用的 Provider-neutral 标的目录构建器。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")


class CatalogBuildError(RuntimeError):
    """所有目录 Provider 均失败。"""


def _bounded_call(operation: Callable[[], Any], timeout_seconds: float = 60.0) -> Any:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="catalog-provider")
    future = executor.submit(operation)
    try:
        return future.result(timeout=timeout_seconds)
    except FuturesTimeoutError as exc:
        future.cancel()
        raise CatalogBuildError("Provider 目录请求超时") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


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
        raise CatalogBuildError("Provider 目录响应缺少代码或名称字段")
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
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """抓取并合并目录；至少一个 Provider 成功才生成新 snapshot。"""

    merged: dict[tuple[str, str, str], dict[str, str]] = {}
    failures: dict[str, str] = {}
    for provider_id, loader in (loaders or PROVIDER_CATALOG_LOADERS).items():
        try:
            provider_items = _bounded_call(loader)
            if not provider_items:
                raise CatalogBuildError("Provider 返回空目录")
            for item in provider_items:
                key = (
                    item["canonicalCode"],
                    item["market"],
                    item["instrumentType"],
                )
                merged.setdefault(key, item)
        except Exception as exc:  # Provider 原始错误只写日志和稳定诊断。
            logger.warning("Catalog provider failed provider=%s: %s", provider_id, exc)
            failures[provider_id] = "catalog_provider_unavailable"
    if not merged:
        raise CatalogBuildError("所有 Catalog Provider 均不可用")
    return [merged[key] for key in sorted(merged)], failures
