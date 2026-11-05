"""新闻共享合同、来源关联与不可用语义的离线验收。"""

import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest
from pydantic import ValidationError
from src.services.thesis_ledger_news_contract_v1 import NewsRequest, NewsResponse
from src.services.thesis_ledger_news_identity import news_item_id

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads((ROOT / "contracts/thesis-ledger/news-search-v1.samples.json").read_text())
SAMPLES = {sample["name"]: sample for sample in FIXTURES["samples"]}


@pytest.mark.parametrize("sample", FIXTURES["samples"], ids=lambda sample: sample["name"])
def test_shared_examples(sample):
    if not sample["requestValid"]:
        with pytest.raises(ValidationError):
            NewsRequest.model_validate(sample["request"])
        return
    NewsRequest.model_validate(sample["request"])
    NewsResponse.model_validate(sample["response"])


def test_empty_unknown_reposts_and_untrusted_text_remain_distinct():
    empty = NewsResponse.model_validate(SAMPLES["empty"]["response"])
    assert empty.status == "available" and not empty.items and empty.upstreamAttempts == 1
    unknown = NewsResponse.model_validate(SAMPLES["unknown_published_time"]["response"])
    assert unknown.items[0].publishedAt is None and unknown.items[0].sourceAvailableAt is None
    assert unknown.status == "partial" and not unknown.coverage.complete
    reposts = NewsResponse.model_validate(SAMPLES["repost_evidence_group"]["response"])
    assert len(reposts.items) == 2 and reposts.coverage.uniqueEvidenceCount == 1
    assert all(item.originalUrl in item.sourceUrls for item in reposts.items)
    text = NewsResponse.model_validate(SAMPLES["untrusted_source_text"]["response"])
    assert "transferFunds" in text.items[0].excerpt
    assert not hasattr(text, "toolCalls")


@pytest.mark.parametrize("field,value", [
    ("limit", 0), ("limit", 51), ("requestBudgetMs", 30001), ("cursor", ""),
    ("accountId", "private"), ("prompt", "instructions"), ("holdings", []),
])
def test_request_rejects_private_material_and_unbounded_work(field, value):
    request = {**SAMPLES["available"]["request"], field: value}
    with pytest.raises(ValidationError):
        NewsRequest.model_validate(request)


@pytest.mark.parametrize("case", [
    "counts", "empty_failure", "not_admitted_attempt", "unknown_available", "truncation",
    "naive_time", "future_publication", "duplicate_url", "forged_id", "wrong_provider",
    "private_output", "unsafe_url", "reversed_window",
])
def test_response_does_not_upgrade_incomplete_or_forged_evidence(case):
    value = copy.deepcopy(SAMPLES["available"]["response"])
    if case == "counts":
        value["coverage"]["returnedCount"] = 2
    elif case == "empty_failure":
        value = copy.deepcopy(SAMPLES["empty"]["response"])
        value["errors"] = [{"code": "upstream_failure", "message": "受控失败"}]
    elif case == "not_admitted_attempt":
        value = copy.deepcopy(SAMPLES["not_admitted"]["response"])
        value["upstreamAttempts"] = 1
    elif case == "unknown_available":
        value = copy.deepcopy(SAMPLES["unknown_published_time"]["response"])
        value["status"] = "available"
    elif case == "truncation":
        value["nextCursor"] = "next"
    elif case == "naive_time":
        value["items"][0]["sourceAvailableAt"] = "2026-10-17T08:00:00"
    elif case == "future_publication":
        value["items"][0]["publishedAt"] = "2026-10-20T08:00:00Z"
    elif case == "duplicate_url":
        second = copy.deepcopy(value["items"][0])
        second.update(sourceItemId="duplicate", url=second["url"] + "?utm_source=duplicate")
        second["id"] = news_item_id(second["providerId"], second["sourceItemId"], second["url"])
        value["items"].append(second)
        value["coverage"].update(returnedCount=2, examinedCount=2)
    elif case == "forged_id":
        value["items"][0]["id"] = "evidence-999"
    elif case == "wrong_provider":
        value["route"]["providerId"] = "different-source"
    elif case == "private_output":
        value["accountId"] = "private"
    elif case == "unsafe_url":
        value["items"][0]["url"] = "javascript:transferFunds()"
    elif case == "reversed_window":
        value["coverage"].update(publishedStart="2026-10-18T08:00:00Z", publishedEnd="2026-10-17T08:00:00Z")
    with pytest.raises(ValidationError):
        NewsResponse.model_validate(value)


def test_dto_import_does_not_load_native_product_configuration_or_model_sdks():
    code = """
import sys
from importlib.abc import MetaPathFinder
class Block(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {'src.config', 'src.analyzer', 'src.search_service'} or fullname.split('.')[0] in {'litellm', 'openai', 'anthropic', 'google'}:
            raise AssertionError('新闻 DTO 触及产品或模型依赖')
sys.meta_path.insert(0, Block())
from src.services.thesis_ledger_news_contract_v1 import NewsRequest
assert 'src.config' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


def test_existing_capability_registry_correlates_request_window_and_target():
    from src.services.thesis_ledger_news_capability import news_capability_contract
    from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
    from src.services.thesis_ledger_capability_registry import (
        CapabilityRegistry, ExactKey, QueryBinding, SourceDefinition,
    )
    raw = copy.deepcopy(SAMPLES["available"]["response"])
    request = NewsRequest.model_validate(SAMPLES["available"]["request"])
    contract = news_capability_contract()
    binding = QueryBinding(contract, ExactKey("CN", "STOCK", "NEWS_SEARCH"),
                           "controlled-search", "news-parser-v1", "source-v1", lambda _q, _c: raw)
    source = SourceDefinition("controlled-news", lambda: {}, lambda _c: object(), (binding,))
    registry = CapabilityRegistry((source,))
    key = binding.key.route_key()
    target = {"providerId": "controlled-news", "upstreamSource": "controlled-search"}
    result = registry.query(contract, request, key, target, ReadContext(None))
    assert result.status == "available"
    raw["requestId"] = "different-request"
    with pytest.raises(CapabilityContractError) as error:
        registry.query(contract, request, key, target, ReadContext(None))
    assert error.value.code == "invalid_response"
