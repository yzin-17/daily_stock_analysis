"""新闻的现行数据执行桥接：固定策略、准入、凭据与响应重放。"""

from dataclasses import dataclass, field
import math
import time

from src.data_config import DataConfigurationError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_current_data_route import select_current_data_targets
from src.services.thesis_ledger_feature_policy import feature_provider_epoch
from src.services.thesis_ledger_news_cache import news_cache_key, news_replay_cache
from src.services.thesis_ledger_news_instruments import resolve_public_news_instrument
from src.services.thesis_ledger_news_pagination import NEWS_CURSORS
from src.services.thesis_ledger_news_scope import news_scope
from src.services.thesis_ledger_price_route_access import configured_data_route


@dataclass
class NewsTrace:
    deadline: float
    effective: object = None
    attempts: int = 0
    new_attempts: int = 0
    errors: list = field(default_factory=list)
    last_response: object = None

    def record(self, response, *, fresh):
        self.attempts += response.upstreamAttempts
        if fresh:
            self.new_attempts += response.upstreamAttempts
        self.last_response = response
        if response.status == "unavailable":
            self.errors.extend(response.errors)


def build_news_operation(runtime, query, trace, error_type):
    effective = runtime.store.effective_policy_v3()
    trace.effective = effective
    key, symbol = news_scope(query)
    targets = select_current_data_targets(effective, capability=query.capability,
                                          instrument_type=key["assetType"], symbol=symbol)
    if query.capability == "NEWS_FEED" and query.providerId is not None:
        targets = [target for target in targets if target[0] == query.providerId]
        if not targets:
            raise error_type("not_admitted", "所选市场快讯来源不在当前有效路由中")
    providers = [provider for provider, _, _ in targets]
    epochs = feature_provider_epoch(runtime.store, providers)
    snapshots = {provider: runtime.store.provider_credential_snapshot(provider) for provider in providers}
    cursor_target = None
    if query.cursor is not None:
        try:
            cursor = NEWS_CURSORS.get(query.cursor)
            cursor_target = (cursor.provider, cursor.source)
        except CapabilityContractError as error:
            raise error_type(error.code, str(error)) from None
    cache = news_replay_cache(runtime)

    def operation(provider, adapter, source):
        if cursor_target is not None and cursor_target != (provider, source):
            raise error_type("invalid_request", "新闻分页不能更换来源，请重新查询")
        remaining = math.floor((trace.deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise error_type("timeout", "新闻总请求预算已耗尽")
        target = {"providerId": provider, "upstreamSource": source}
        snapshot = snapshots[provider]
        current = runtime.store.effective_policy_v3()
        if (current.get("revision") != effective.get("revision")
                or feature_provider_epoch(runtime.store, providers) != epochs
                or runtime.store.provider_credential_snapshot(provider) != snapshot):
            raise error_type("not_admitted", "新闻执行前控制或凭据已变化")
        admission = runtime._current_market_v3_admission(key, target, credential_snapshot=snapshot)
        if not configured_data_route(key, target) and (not admission or admission.get("admissionState") != "admitted"):
            raise error_type("not_admitted", "新闻精确来源尚未准入")
        binding = CAPABILITY_SOURCES.resolve(key, target)
        if binding is None:
            raise error_type("unsupported", "新闻来源缺少精确绑定")
        identity = None
        if query.capability == "NEWS_SEARCH":
            try:
                identity = resolve_public_news_instrument(query.instrument, runtime.store.database_path)
            except DataConfigurationError:
                raise error_type("configuration_failure", "新闻公开目录读取失败") from None
            if identity is None:
                raise error_type("ambiguous_instrument", "公开目录无法唯一核验本次新闻标的")
        from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
        from src.services.thesis_ledger_market_v3_revisions import MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION
        definition = CAPABILITY_SOURCES.source(provider)
        if definition is not None and definition.manifest().get("requiresCredential") is False:
            credential_revision = MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION
        else:
            credential_revision = _provider_credential_revision_from_snapshot(snapshot)
        if credential_revision is None:
            raise error_type("configuration_failure", "新闻凭据修订不可用")
        replay_key = news_cache_key(
            query, policy_revision=effective["revision"], target=target, binding=binding,
            credential_revision=credential_revision, epochs=epochs, identity=identity,
        )
        if time.monotonic() >= trace.deadline:
            raise error_type("timeout", "新闻总预算在缓存读取前已耗尽")
        response = cache.get(replay_key)
        fresh = response is None
        if fresh:
            remaining = math.floor((trace.deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise error_type("timeout", "新闻总请求预算已耗尽")
            bounded = query.model_copy(update={"requestBudgetMs": min(query.requestBudgetMs, remaining)})
            context = ReadContext(adapter, snapshot, control_epoch=(effective["revision"], epochs[provider][0]),
                                  database_path=runtime.store.database_path)
            try:
                response = CAPABILITY_SOURCES.query(binding.contract, bounded, key, target, context)
            except CapabilityContractError as error:
                raise error_type(error.code, str(error)) from None
            if not trace.errors:
                cache.put(replay_key, response)
        else:
            response = binding.contract.correlate_result(query, response, target)
        trace.record(response, fresh=fresh)
        if time.monotonic() >= trace.deadline:
            raise error_type("timeout", "新闻总预算在来源返回时已耗尽")
        if response.status == "unavailable":
            code = response.errors[0].code
            raise error_type(code, "新闻来源未完成有效检索", retryable=code in {"timeout", "upstream_failure", "rate_limited"})
        return response.model_dump()

    return operation
