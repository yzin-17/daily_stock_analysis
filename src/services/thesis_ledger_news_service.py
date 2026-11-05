"""已选定新闻来源的身份、独立配置、预算与合同组合。"""

from dataclasses import replace
from datetime import datetime, timezone
import math
import time

from src.data_config import DataConfigurationError, get_data_config
from src.services.thesis_ledger_news_contract_v1 import NewsError
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_identity import news_time
from src.services.thesis_ledger_news_instruments import resolve_public_news_instrument
from src.services.thesis_ledger_news_records import RawNewsRead
from src.services.thesis_ledger_news_results import build_news_response
from src.services.thesis_ledger_news_sources import news_settings, PROVIDERS

NEWS_UPSTREAMS = {provider: f"{provider}-news-search" for provider in PROVIDERS}


def search_news(
    request, route, *, values=None, database_path=None, config_reader=get_data_config,
    identity_reader=resolve_public_news_instrument, executor=execute_news_search,
):
    started = time.monotonic()
    settings = None

    def result(read):
        secrets = settings.api_keys + settings.base_urls if settings is not None else ()
        return build_news_response(request, route, read, fetched_at=datetime.now(timezone.utc).isoformat(),
                                   secrets=secrets)

    def reject(code, message):
        error = NewsError(code=code, message=message, providerId=route.providerId,
                          upstreamSource=route.upstreamSource)
        return result(RawNewsRead(errors=(error,)))

    if NEWS_UPSTREAMS.get(route.providerId) != route.upstreamSource:
        return reject("unsupported", "新闻来源未匹配声明的精确绑定")
    try:
        config = config_reader() if values is None or database_path is None else None
        settings = news_settings(route.providerId, config=config, values=values)
        path = database_path if database_path is not None else config.database_path
        identity = identity_reader(request.instrument, path)
    except DataConfigurationError:
        return reject("configuration_failure", "新闻来源或公开身份配置读取失败")
    except Exception:
        return reject("configuration_failure", "新闻数据装配失败")
    if identity is None:
        return reject("ambiguous_instrument", "公开目录无法唯一核验市场、资产类型与规范标的")
    remaining = request.requestBudgetMs - math.ceil((time.monotonic() - started) * 1000)
    if remaining <= 0:
        return reject("timeout", "新闻请求预算在执行前已耗尽")
    days = 7
    reasons = ()
    if request.publishedStart is not None:
        days = max(1, math.ceil((datetime.now(timezone.utc) - news_time(request.publishedStart)).total_seconds() / 86400))
        if days > 365:
            days = 365
            reasons = ("search_window_exceeds_one_year",)
    read = executor(settings, identity.query(), limit=request.limit, days=days,
                    request_budget_ms=remaining, cursor=request.cursor)
    source_identity = {"providerId": route.providerId, "upstreamSource": route.upstreamSource}
    read = replace(read, errors=tuple(error.model_copy(update=source_identity) for error in read.errors))
    if reasons:
        read = replace(read, missing_reasons=read.missing_reasons + reasons)
    return result(read)
