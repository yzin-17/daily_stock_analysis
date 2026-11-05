"""新闻精确来源声明；默认关闭、独立准入，不执行注册时取数。"""

from dataclasses import dataclass, field
from functools import partial
import json

from src.data_config import DataConfigurationError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, EnvironmentCredentialPolicy
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_news_capability import news_capability_contract
from src.services.thesis_ledger_news_contract_v1 import NewsRoute
from src.services.thesis_ledger_news_service import NEWS_UPSTREAMS, search_news
from src.services.thesis_ledger_news_sources import news_settings, PROVIDERS

CONTRACT = news_capability_contract()
ADAPTER_REVISION = "dsa-news-query-v1"
SOURCE_REVISION = "selected-search-raw-results-v1"
PUBLIC_ROUTE_REVISION = f"{ADAPTER_REVISION}:{SOURCE_REVISION}"
MARKET_ASSETS = (("CN", "STOCK"), ("CN", "ETF"), ("CN", "INDEX"), ("CN", "MUTUAL_FUND"),
                 ("HK", "STOCK"), ("US", "STOCK"))


@dataclass(frozen=True)
class NewsAdapter:
    provider_id: str
    values: dict = field(repr=False)


def environment_credentials(provider_id):
    settings = news_settings(provider_id)
    if not settings.configured():
        return {}
    name = "baseUrls" if provider_id == "searxng" else "apiKeys"
    values = settings.base_urls if provider_id == "searxng" else settings.api_keys
    return {name: json.dumps(values, ensure_ascii=False)}


def normalize_credential(name, value):
    if not value.strip():
        return ""
    provider = "searxng" if name == "baseUrls" else "tavily"
    try:
        settings = news_settings(provider, values={name: value})
        if not settings.configured():
            raise DataConfigurationError("新闻凭据为空")
    except DataConfigurationError:
        raise ValueError("新闻凭据字段必须是有效的非空 JSON 字符串列表") from None
    values = settings.base_urls if name == "baseUrls" else settings.api_keys
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def factory(provider_id, context):
    snapshot = context.snapshot
    if snapshot is None or snapshot.provider_id != provider_id:
        raise CapabilityContractError("configuration_failure", "新闻来源缺少固定凭据快照")
    return NewsAdapter(provider_id, dict(snapshot.values))


def credential_payload(snapshot):
    settings = news_settings(snapshot.provider_id, values=snapshot.values)
    if not settings.configured():
        return None
    facts = {"provider": snapshot.provider_id, "source": snapshot.source, "method": snapshot.method,
             "values": dict(snapshot.values), "configVersion": snapshot.config_version,
             "credentialVersion": snapshot.credential_version}
    return json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def admission_current(context):
    if context.state != "admitted" or context.payload is None:
        return False
    from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
    from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
    revision = _provider_credential_revision_from_snapshot(context.read_credentials())
    if revision is None:
        return False
    admission = {**context.payload, "admissionState": context.state}
    return _market_v3_admission_matches_current(
        admission, context.key, context.target, context.manifest, credential_revision=revision,
    )


def read(provider_id, request, context):
    if (not isinstance(context.adapter, NewsAdapter) or context.adapter.provider_id != provider_id
            or context.control_epoch is None or context.snapshot is None or context.database_path is None):
        raise CapabilityContractError("configuration_failure", "新闻读取缺少正式控制上下文")
    snapshot = context.snapshot
    if context.adapter.values != dict(snapshot.values):
        raise CapabilityContractError("not_admitted", "新闻适配器凭据与固定快照不一致")
    route = NewsRoute(
        providerId=provider_id, upstreamSource=NEWS_UPSTREAMS[provider_id],
        policyRevision=f"effective:{context.control_epoch[0]}", routeRevision=PUBLIC_ROUTE_REVISION,
        credentialRevision=f"{snapshot.source}:config:{snapshot.config_version}:credential:{snapshot.credential_version}",
    )
    return search_news(request, route, values=context.adapter.values, database_path=context.database_path)


def manifest(provider_id):
    from src.services.provider_manifest import build_provider_manifest
    value = build_provider_manifest(
        provider_id, f"{provider_id} 新闻检索", {"NEWS_SEARCH": ("STOCK", "ETF", "INDEX", "MUTUAL_FUND")},
        requires_credential=True, upstream_sources=((NEWS_UPSTREAMS[provider_id], f"{provider_id} 搜索"),),
        markets=("CN", "HK", "US"), configuration_mode="dsa_environment",
    )
    value["upstreamSources"][0]["capabilities"] = value["capabilities"]
    return value


def definition(provider_id):
    credential_field = "baseUrls" if provider_id == "searxng" else "apiKeys"
    dependencies = ("requests",)
    if provider_id in {"tavily", "serpapi"}:
        dependencies += (provider_id,)
    return SourceDefinition(
        provider_id, partial(manifest, provider_id), partial(factory, provider_id),
        tuple(QueryBinding(CONTRACT, ExactKey(market, asset, "NEWS_SEARCH"), NEWS_UPSTREAMS[provider_id],
                           ADAPTER_REVISION, SOURCE_REVISION, partial(read, provider_id),
                           gated=True, admission_required=True)
              for market, asset in MARKET_ASSETS),
        credentials=(("search", (credential_field,)),), dependency_modules=dependencies,
        factory_scope="provider", require_credential_revision_for_cache=True,
        environment_credential_policy=EnvironmentCredentialPolicy(partial(environment_credentials, provider_id)),
        normalize_credential_value=normalize_credential, credential_revision_kind="hmac",
        credential_revision_payload=credential_payload, policy_admission_guard=admission_current, default_enabled=False,
    )


SOURCES = tuple(definition(provider_id) for provider_id in PROVIDERS)
