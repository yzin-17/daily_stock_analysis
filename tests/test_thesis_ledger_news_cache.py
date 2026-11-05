"""新闻重放的过期、深复制、容量与身份隔离。"""

from dataclasses import replace
import json
from pathlib import Path

import pytest

from data_provider.thesis_ledger_sources.news import SOURCES
from src.services.thesis_ledger_news_cache import NewsReplayCache, news_cache_key
from src.services.thesis_ledger_news_contract_v1 import NewsRequest, NewsResponse
from src.services.thesis_ledger_news_instruments import PublicNewsInstrument

PACK = json.loads((Path(__file__).resolve().parents[1] / "contracts/thesis-ledger/news-search-v1.samples.json").read_text())
SAMPLE = next(item for item in PACK["samples"] if item["name"] == "available")


def test_cache_preserves_original_facts_and_expires_and_copies_mutable_items():
    now = [100.0]
    cache = NewsReplayCache(clock=lambda: now[0])
    response = NewsResponse.model_validate(SAMPLE["response"])
    cache.put("request", response)
    replay = cache.get("request")
    assert replay == response
    replay.items.clear()
    assert cache.get("request").items == response.items
    now[0] += 60
    assert cache.get("request") is None
    partial = response.model_copy(update={"status": "partial"})
    cache.put("partial", partial)
    assert cache.get("partial") is None
    for number in range(65):
        cache.put(str(number), response)
    assert cache.get("0") is None and cache.get("64") == response


@pytest.mark.parametrize("changed", ["requestId", "window", "limit", "cursor", "market", "asset", "symbol",
                                     "policy", "target", "adapter", "source", "credential", "epoch", "identity"])
def test_business_and_control_identity_changes_never_replay_old_data(changed):
    query = NewsRequest.model_validate(SAMPLE["request"])
    facts = {"policy_revision": 1, "target": {"providerId": "tavily", "upstreamSource": "tavily-news-search"},
             "binding": SOURCES[0].bindings[0], "credential_revision": "private-internal-revision",
             "epochs": {"tavily": (1, True)},
             "identity": PublicNewsInstrument("600519.SH", "CN", "STOCK", "贵州茅台")}
    original = news_cache_key(query, **facts)
    if changed in {"requestId", "limit", "cursor"}:
        query = query.model_copy(update={changed: {"requestId": "different", "limit": 2, "cursor": "page-2"}[changed]})
    elif changed == "window":
        query = query.model_copy(update={"publishedStart": "2026-10-18T00:00:00Z"})
    elif changed in {"market", "asset", "symbol"}:
        fields = {"market": "market", "asset": "assetType", "symbol": "symbol"}
        query = query.model_copy(update={"instrument": query.instrument.model_copy(update={fields[changed]: "different"})})
    elif changed in {"adapter", "source"}:
        facts["binding"] = replace(facts["binding"], **{changed + "_revision": "different"})
    else:
        key = {"policy": "policy_revision", "target": "target", "credential": "credential_revision",
               "epoch": "epochs", "identity": "identity"}[changed]
        value = {"policy": 2, "target": {"providerId": "brave", "upstreamSource": "brave-news-search"},
                 "credential": "different-private-revision", "epoch": {"tavily": (2, True)},
                 "identity": PublicNewsInstrument("600519.SH", "CN", "STOCK", "公开更名")}[changed]
        facts[key] = value
    assert news_cache_key(query, **facts) != original
