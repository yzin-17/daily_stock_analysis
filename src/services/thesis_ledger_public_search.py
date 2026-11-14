"""只读研究的独立 WEB_SEARCH 合同，复用固定凭据和有界搜索执行。"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4
from urllib.parse import urlsplit
import time

from pydantic import BaseModel, ConfigDict, Field
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import CapabilityContract
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_sources import NewsSearchSettings, news_settings


class SearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    market: Literal["CN", "HK", "US"]
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(ge=1, le=10)
    requestBudgetMs: int = Field(default=12000, ge=1000, le=12000)


class SearchItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str
    url: str
    snippet: str
    publishedAt: str | None


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    status: Literal["ready", "unavailable"]
    market: Literal["CN", "HK", "US"]
    query: str
    provider: str | None
    observedAt: str
    reason: str | None
    items: list[SearchItem] = Field(max_length=10)
    coverage: Literal["last_year"] = "last_year"
    upstreamAttempts: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class PublicSearchSettings(NewsSearchSettings):
    topic: str = "general"


def unavailable(request, reason, *, attempts=0, provider=None):
    return SearchResult(status="unavailable", market=request.market, query=request.query, provider=provider,
                        observedAt=datetime.now(timezone.utc).isoformat(), reason=reason, items=[], upstreamAttempts=attempts)


def read_search(provider_id, request, context, *, executor=execute_news_search):
    snapshot = context.snapshot
    if snapshot is None or snapshot.provider_id != provider_id or context.control_epoch is None:
        raise CapabilityContractError("configuration_failure", "网页搜索缺少正式控制上下文")
    if getattr(context.adapter, "values", None) != dict(snapshot.values):
        raise CapabilityContractError("not_admitted", "网页搜索凭据与固定快照不一致")
    base = news_settings(provider_id, values=snapshot.values)
    settings = PublicSearchSettings(base.provider_id, base.api_keys, base.base_urls)
    read = executor(settings, request.query, limit=request.limit, days=365, request_budget_ms=request.requestBudgetMs)
    if not read.success or read.errors:
        code = read.errors[0].code if read.errors else "upstream_failure"
        raise CapabilityContractError(code, "网页搜索未返回完整可用结果", retryable=False,
                                      diagnostics={"upstreamAttempts": read.upstream_attempts})
    items = []
    for row in read.records:
        url = urlsplit(row.url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise CapabilityContractError("invalid_response", "网页搜索链接无效", diagnostics={"upstreamAttempts": read.upstream_attempts})
        items.append(SearchItem(title=row.title[:1000], url=row.url, snippet=(row.excerpt or "")[:8000], publishedAt=row.published_at))
        if len(items) >= request.limit:
            break
    return SearchResult(status="ready", market=request.market, query=request.query, provider=provider_id,
                        observedAt=datetime.now(timezone.utc).isoformat(), reason=None, items=items, upstreamAttempts=read.upstream_attempts)


def _binding(request, key, _target):
    if key != {"kind": "data", "market": request.market, "assetType": "STOCK", "capability": "WEB_SEARCH"}:
        raise CapabilityContractError("invalid_request", "网页搜索市场范围不一致")


def _correlate(request, result, target):
    if (result.market != request.market or result.query != request.query or len(result.items) > request.limit
            or result.provider != target.get("providerId")):
        raise CapabilityContractError("invalid_response", "网页搜索结果身份不一致")
    return result


SEARCH_CONTRACT = CapabilityContract("WEB_SEARCH", SearchRequest, SearchResult.model_validate,
                                     validate_binding=_binding, correlate_result=_correlate)


def execute_public_search(runtime, query):
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerGatewayError, ProviderCallError
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_current_data_route import CurrentDataRouteError, select_current_data_targets
    from src.services.thesis_ledger_feature_policy import feature_provider_epoch
    started = time.monotonic()
    attempts, last_provider = 0, None
    key = {"kind": "data", "market": query.market, "assetType": "STOCK", "capability": "WEB_SEARCH"}
    # 只用于既有市场路由分派，不声明证券身份，也不发送给上游检索服务。
    symbol = "__PUBLIC_SEARCH__." + query.market
    request = ThesisLedgerDataRequest("WEB_SEARCH", symbol, instrument_type="STOCK", limit=query.limit,
                                      request_id=str(uuid4()), parameters=query.model_dump())
    try:
        effective = runtime.store.effective_policy_v3()
        targets = select_current_data_targets(effective, capability="WEB_SEARCH", instrument_type="STOCK", symbol=symbol)
        epochs = feature_provider_epoch(runtime.store, [p for p, _, _ in targets])
        snapshots = {p: runtime.store.provider_credential_snapshot(p) for p, _, _ in targets}

        def operation(provider, adapter, source):
            nonlocal attempts, last_provider
            last_provider = provider
            if time.monotonic() - started >= 12:
                raise ProviderCallError("timeout", "网页搜索总预算已耗尽")
            target = {"providerId": provider, "upstreamSource": source}
            if runtime.store.effective_policy_v3().get("revision") != effective.get("revision"):
                raise ProviderCallError("not_admitted", "网页搜索控制版本已变化")
            from src.services.thesis_ledger_capability_contracts import ReadContext
            binding = CAPABILITY_SOURCES.resolve(key, target)
            if binding is None:
                raise ProviderCallError("unsupported", "网页搜索来源没有精确绑定")
            try:
                remaining = int((12 - (time.monotonic() - started)) * 1000)
                if remaining < 1000:
                    raise ProviderCallError("timeout", "网页搜索总预算已耗尽")
                bounded = query.model_copy(update={"requestBudgetMs": remaining})
                result = CAPABILITY_SOURCES.query(binding.contract, bounded, key, target, ReadContext(adapter, snapshots[provider],
                    control_epoch=(effective["revision"], epochs[provider][0]), database_path=runtime.store.database_path))
            except CapabilityContractError as error:
                attempts += int(error.diagnostics.get("upstreamAttempts", 0))
                raise ProviderCallError(error.code, str(error), retryable=False) from None
            attempts += result.upstreamAttempts
            return result.model_dump()

        result = runtime._execute_request_with_boundary(request, "WEB_SEARCH", "STOCK", operation)
        if time.monotonic() - started >= 12:
            return unavailable(query, "timeout", attempts=attempts, provider=last_provider)
        if feature_provider_epoch(runtime.store, [result.provider]) != {result.provider: epochs[result.provider]}:
            return unavailable(query, "not_admitted", attempts=attempts, provider=last_provider)
        return SearchResult.model_validate({**result.value, "upstreamAttempts": attempts})
    except (ThesisLedgerGatewayError, ProviderCallError, CurrentDataRouteError, CapabilityContractError) as error:
        code = getattr(error, "code", "upstream_failure")
        return unavailable(query, "not_admitted" if code == "NO_ELIGIBLE_PROVIDER" else code, attempts=attempts, provider=last_provider)
