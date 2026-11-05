"""仅 NVIDIA 官方消息的无 Key 来源；默认关闭，精确准入后读取。"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import math
import time

from data_provider.nvidia_news_rss import NvidiaNewsSettings, nvidia_news_worker
from src.data_config import DataConfigurationError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_news_capability import news_capability_contract
from src.services.thesis_ledger_news_contract_v1 import NewsError, NewsRoute
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_instruments import resolve_public_news_instrument
from src.services.thesis_ledger_news_records import RawNewsRead
from src.services.thesis_ledger_news_results import build_news_response

PROVIDER_ID = "nvidia-news"
UPSTREAM = "nvidia-newsroom-rss"
ADAPTER_REVISION = "nvidia-rss-news-v1"
SOURCE_REVISION = "nvidia-official-finite-feed-v1"
CONTRACT = news_capability_contract()


@dataclass(frozen=True)
class NvidiaNewsAdapter:
    values: dict = field(default_factory=dict, repr=False)


def factory(context):
    return NvidiaNewsAdapter(dict(context.snapshot.values) if context.snapshot is not None else {})


def admission_current(context):
    if context.state != "admitted" or context.payload is None:
        return False
    from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
    return _market_v3_admission_matches_current(
        {**context.payload, "admissionState": context.state}, context.key, context.target, context.manifest,
    )


def read(request, context):
    if (not isinstance(context.adapter, NvidiaNewsAdapter) or context.control_epoch is None
            or context.database_path is None):
        raise CapabilityContractError("configuration_failure", "官方新闻缺少固定控制上下文")
    started = time.monotonic()
    route = NewsRoute(providerId=PROVIDER_ID, upstreamSource=UPSTREAM,
                      policyRevision=f"effective:{context.control_epoch[0]}",
                      routeRevision=f"{ADAPTER_REVISION}:{SOURCE_REVISION}", credentialRevision="not-required")

    def result(value):
        return build_news_response(request, route, value, fetched_at=datetime.now(timezone.utc).isoformat())

    def reject(code, message):
        error = NewsError(code=code, message=message, providerId=PROVIDER_ID, upstreamSource=UPSTREAM)
        return result(RawNewsRead(errors=(error,)))

    if (request.instrument.symbol, request.instrument.market, request.instrument.assetType) != ("NVDA.US", "US", "STOCK"):
        return reject("not_covered", "本来源仅提供 NVIDIA 官方消息")
    try:
        identity = resolve_public_news_instrument(request.instrument, context.database_path)
    except DataConfigurationError:
        return reject("configuration_failure", "官方新闻公开身份读取失败")
    if identity is None:
        return reject("ambiguous_instrument", "公开目录无法核验 NVIDIA 规范身份")
    remaining = request.requestBudgetMs - math.ceil((time.monotonic() - started) * 1000)
    raw = execute_news_search(NvidiaNewsSettings(), identity.query(), limit=request.limit, days=7,
                              request_budget_ms=remaining, cursor=request.cursor, worker=nvidia_news_worker)
    raw = replace(raw, missing_reasons=raw.missing_reasons + ("issuer_feed_not_exhaustive",),
                  errors=tuple(error.model_copy(update={"providerId": PROVIDER_ID, "upstreamSource": UPSTREAM})
                               for error in raw.errors))
    return result(raw)


def manifest():
    from src.services.provider_manifest import build_provider_manifest
    value = build_provider_manifest(
        PROVIDER_ID, "NVIDIA 官方新闻（个人非商业）", {"NEWS_SEARCH": ("STOCK",)},
        markets=("US",), upstream_sources=((UPSTREAM, "NVIDIA Newsroom RSS · NVDA.US"),),
    )
    value["upstreamSources"][0]["capabilities"] = value["capabilities"]
    return value


SOURCE = SourceDefinition(
    PROVIDER_ID, manifest, factory,
    (QueryBinding(CONTRACT, ExactKey("US", "STOCK", "NEWS_SEARCH"), UPSTREAM,
                  ADAPTER_REVISION, SOURCE_REVISION, read, gated=True, admission_required=True),),
    dependency_modules=("requests",), factory_scope="provider",
    policy_admission_guard=admission_current, default_enabled=False,
)
