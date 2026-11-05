"""HiThink 新闻精确绑定：沿用已有账号快照和 HMAC 准入。"""

from dataclasses import replace
from datetime import datetime, timezone
import math
import re
import time

from data_provider.hithink_news import HithinkNewsSettings, hithink_news_worker
from src.data_config import DataConfigurationError
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding
from src.services.thesis_ledger_news_capability import news_capability_contract
from src.services.thesis_ledger_news_contract_v1 import NewsError, NewsRoute
from src.services.thesis_ledger_news_execution import execute_news_search
from src.services.thesis_ledger_news_instruments import resolve_public_news_instrument
from src.services.thesis_ledger_news_records import RawNewsRead
from src.services.thesis_ledger_news_results import build_news_response

UPSTREAM = "hithink-news-stock-timeline"
ADAPTER_REVISION = "hithink-stock-news-v1"
SOURCE_REVISION = "hithink-stock-timeline-first-page-v1"


def read(request, context):
    if context.control_epoch is None or context.database_path is None or context.snapshot is None:
        raise CapabilityContractError("configuration_failure", "HiThink 新闻缺少固定控制上下文")
    started = time.monotonic()
    snapshot = context.snapshot
    settings = HithinkNewsSettings(snapshot.values.get("apiKey", ""))
    route = NewsRoute(providerId="hithink", upstreamSource=UPSTREAM,
                      policyRevision=f"effective:{context.control_epoch[0]}",
                      routeRevision=f"{ADAPTER_REVISION}:{SOURCE_REVISION}",
                      credentialRevision=f"{snapshot.source}:config:{snapshot.config_version}:credential:{snapshot.credential_version}")

    def result(value):
        return build_news_response(request, route, value, fetched_at=datetime.now(timezone.utc).isoformat(),
                                   secrets=(settings.api_key,))

    def reject(code, message):
        return result(RawNewsRead(errors=(NewsError(code=code, message=message, providerId="hithink",
                                                    upstreamSource=UPSTREAM),)))

    if (request.instrument.market != "CN" or request.instrument.assetType != "STOCK"
            or not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", request.instrument.symbol)):
        return reject("not_covered", "本来源仅提供规范 A 股代码的个股新闻")
    try:
        identity = resolve_public_news_instrument(request.instrument, context.database_path)
    except DataConfigurationError:
        return reject("configuration_failure", "新闻公开身份读取失败")
    if identity is None:
        return reject("ambiguous_instrument", "公开目录无法核验新闻标的身份")
    remaining = request.requestBudgetMs - math.ceil((time.monotonic() - started) * 1000)
    raw = execute_news_search(settings, identity.symbol, limit=request.limit, days=3,
                              request_budget_ms=remaining, cursor=request.cursor, worker=hithink_news_worker)
    raw = replace(raw, missing_reasons=raw.missing_reasons + ("timeline_window_not_exhaustive",),
                  errors=tuple(error.model_copy(update={"providerId": "hithink", "upstreamSource": UPSTREAM})
                               for error in raw.errors))
    return result(raw)


BINDING = QueryBinding(news_capability_contract(), ExactKey("CN", "STOCK", "NEWS_SEARCH"), UPSTREAM,
                       ADAPTER_REVISION, SOURCE_REVISION, read, gated=True, admission_required=True)
