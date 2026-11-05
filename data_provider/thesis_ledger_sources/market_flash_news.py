"""市场快讯的独立精确来源；只在当前策略、启停和有限准入下执行。"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from data_provider.market_flash_news import FlashNewsSettings, flash_news_worker
from data_provider.market_reference_bindings import reference_bindings, reference_manifest
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import CapabilityContract, ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_news_contract_v1 import NewsRoute
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_feed_contract_v1 import NewsFeedRequest, NewsFeedResponse, build_news_feed_response

ADAPTER_REVISION = "market-flash-metadata-v1"
PROFILES = {
    "cls": ("cls-roll", "财联社快讯", "cls-web-roll-signed-page-v1"),
    "wallstreetcn": ("wallstreetcn-a-stock", "华尔街见闻快讯", "wallstreetcn-a-stock-first-page-v1"),
}


@dataclass(frozen=True)
class FlashAdapter:
    provider_id: str


def _binding(request, key, target):
    if (key != {"kind": "data", "market": request.market, "assetType": "STOCK", "capability": "NEWS_FEED"}
            or request.providerId is not None and request.providerId != target.get("providerId")):
        raise CapabilityContractError("unsupported_capability", "快讯仅覆盖中国股票市场")


def _correlate(request, response, target):
    if (response.requestId != request.requestId or response.market != request.market
            or response.coverage.publishedStart != request.publishedStart
            or response.coverage.publishedEnd != request.publishedEnd or len(response.items) > request.limit
            or response.route is not None and (
                response.route.providerId != target["providerId"] or response.route.upstreamSource != target["upstreamSource"]
                or request.providerId is not None and response.route.providerId != request.providerId)):
        raise CapabilityContractError("invalid_response", "市场快讯响应未绑定本次范围或来源")
    return response


CONTRACT = CapabilityContract("NEWS_FEED", NewsFeedRequest, NewsFeedResponse.model_validate,
                              validate_binding=_binding, correlate_result=_correlate)


def admission_current(context):
    if context.state != "admitted" or context.payload is None:
        return False
    from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
    return _market_v3_admission_matches_current(
        {**context.payload, "admissionState": context.state}, context.key, context.target, context.manifest,
    )


def read(request, context):
    if not isinstance(context.adapter, FlashAdapter) or context.control_epoch is None:
        raise CapabilityContractError("configuration_failure", "市场快讯缺少固定控制上下文")
    provider = context.adapter.provider_id
    upstream, _label, revision = PROFILES[provider]
    route = NewsRoute(providerId=provider, upstreamSource=upstream,
                      policyRevision=f"effective:{context.control_epoch[0]}",
                      routeRevision=f"{ADAPTER_REVISION}:{revision}", credentialRevision="not-required")
    raw = execute_news_search(FlashNewsSettings(provider), "CN.STOCK", limit=request.limit, days=3,
                              request_budget_ms=request.requestBudgetMs, cursor=request.cursor, worker=flash_news_worker)
    raw = replace(raw, missing_reasons=raw.missing_reasons + ("market_feed_first_page_not_exhaustive",),
                  errors=tuple(error.model_copy(update={"providerId": provider, "upstreamSource": upstream})
                               for error in raw.errors))
    return build_news_feed_response(request, route, raw, fetched_at=datetime.now(timezone.utc).isoformat())


def source(provider):
    upstream, label, revision = PROFILES[provider]
    references = reference_bindings(provider)

    def manifest():
        from src.services.provider_manifest import build_provider_manifest
        channel = "全市场电报" if provider == "cls" else "A 股频道"
        value = build_provider_manifest(provider, label, {"NEWS_FEED": ("STOCK",)},
                                        upstream_sources=((upstream, label + " · " + channel),))
        value["upstreamSources"][0]["capabilities"] = dict(value["capabilities"])
        return reference_manifest(value, references)

    return SourceDefinition(
        provider, manifest, lambda _context: FlashAdapter(provider),
        (*references, QueryBinding(CONTRACT, ExactKey("CN", "STOCK", "NEWS_FEED"), upstream,
                      ADAPTER_REVISION, revision, read, gated=True, admission_required=True),),
        dependency_modules=("curl_cffi",), factory_scope="provider",
        policy_admission_guard=admission_current, default_enabled=False,
    )


SOURCES = tuple(source(provider) for provider in PROFILES)
