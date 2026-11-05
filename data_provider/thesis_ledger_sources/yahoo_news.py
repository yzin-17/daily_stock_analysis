"""既有 Yahoo 来源的 US 股票新闻精确绑定，保留独立准入。"""

from dataclasses import replace
from datetime import datetime, timezone
import math
import re
import time

from data_provider.yahoo_news import YahooNewsSettings, yahoo_news_worker
from src.data_config import DataConfigurationError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding
from src.services.thesis_ledger_news_capability import news_capability_contract
from src.services.thesis_ledger_news_contract_v1 import NewsError, NewsRoute
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_instruments import resolve_public_news_instrument
from src.services.thesis_ledger_news_records import RawNewsRead
from src.services.thesis_ledger_news_results import build_news_response

UPSTREAM = "yahoo-search-news"
ADAPTER_REVISION = "yfinance-search-news-1-7-0-v1"
SOURCE_REVISION = "yahoo-search-recent-news-v1"


def admission_current(context):
    if context.key.get("capability") != "NEWS_SEARCH":
        return True
    if context.state != "admitted" or context.payload is None:
        return False
    from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
    return _market_v3_admission_matches_current(
        {**context.payload, "admissionState": context.state}, context.key, context.target, context.manifest,
    )


def read(request, context):
    if context.control_epoch is None or context.database_path is None or context.snapshot is None:
        raise CapabilityContractError("configuration_failure", "Yahoo 新闻缺少固定控制上下文")
    started = time.monotonic()
    route = NewsRoute(providerId="yfinance", upstreamSource=UPSTREAM,
                      policyRevision=f"effective:{context.control_epoch[0]}",
                      routeRevision=f"{ADAPTER_REVISION}:{SOURCE_REVISION}", credentialRevision="not-required")

    def result(value):
        return build_news_response(request, route, value, fetched_at=datetime.now(timezone.utc).isoformat())

    def reject(code, message):
        return result(RawNewsRead(errors=(NewsError(code=code, message=message, providerId="yfinance",
                                                    upstreamSource=UPSTREAM),)))

    if (request.instrument.market != "US" or request.instrument.assetType != "STOCK"
            or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}\.US", request.instrument.symbol)):
        return reject("not_covered", "本来源仅提供规范美股代码的新闻搜索")
    try:
        identity = resolve_public_news_instrument(request.instrument, context.database_path)
    except DataConfigurationError:
        return reject("configuration_failure", "新闻公开身份读取失败")
    if identity is None:
        return reject("ambiguous_instrument", "公开目录无法核验新闻标的身份")
    remaining = request.requestBudgetMs - math.ceil((time.monotonic() - started) * 1000)
    raw = execute_news_search(YahooNewsSettings(), identity.symbol, limit=request.limit, days=3,
                              request_budget_ms=remaining, cursor=request.cursor, worker=yahoo_news_worker)
    raw = replace(raw, missing_reasons=raw.missing_reasons + ("search_first_page_not_exhaustive",),
                  errors=tuple(error.model_copy(update={"providerId": "yfinance", "upstreamSource": UPSTREAM})
                               for error in raw.errors))
    return result(raw)


BINDING = QueryBinding(news_capability_contract(), ExactKey("US", "STOCK", "NEWS_SEARCH"), UPSTREAM,
                       ADAPTER_REVISION, SOURCE_REVISION, read, gated=True, admission_required=True)
