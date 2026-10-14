"""事件 V3 的精确适配库存；库存不授予来源准入或事件覆盖。"""

from typing import Any, Mapping

from data_provider.tushare_cash_capability import TARGET as TUSHARE_TARGET
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import EventBinding
from data_provider.eastmoney_cash_capability import TARGET as EVENT_TARGET, REVISIONS as EVENT_REVISIONS
from data_provider.eastmoney_split_capability import REVISIONS as SPLIT_REVISIONS
from data_provider.thesis_ledger_sources.rqdata import TARGET as RQDATA_TARGET, REVISIONS as RQDATA_REVISIONS


EVENT_KEY = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"}
SPLIT_KEY = {**EVENT_KEY, "capability": "SPLIT_EVENT"}


def event_adapter_matches(key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    return isinstance(CAPABILITY_SOURCES.resolve(key, target), EventBinding)


def event_admission_reader(key, target):
    binding = CAPABILITY_SOURCES.resolve(key, target)
    return binding.current_admission if isinstance(binding, EventBinding) else None


def event_adapter_revisions(key: Mapping[str, Any], target=None, credential_revision=None) -> dict[str, str] | None:
    if target is not None and not event_adapter_matches(key, target):
        return None
    if target is not None:
        binding = CAPABILITY_SOURCES.resolve(key, target)
        if isinstance(binding, EventBinding):
            return binding.revision_resolver(credential_revision)
    return dict(SPLIT_REVISIONS if dict(key) == SPLIT_KEY else EVENT_REVISIONS)


def iter_event_adapters():
    for key, target, binding in CAPABILITY_SOURCES.routes():
        if isinstance(binding, EventBinding):
            yield key, target, binding.gated
