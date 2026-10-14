"""本地来源目录就绪的有限声明查找；读取与路径由来源拥有。"""

from typing import Any, Mapping

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import QueryBinding
from src.services.thesis_ledger_capability_contracts import CatalogReadinessContext

LOCAL_RULE_CAPABILITIES = frozenset({'HISTORICAL_PUBLIC_FEES', 'HISTORICAL_TRADING_STATUS'})
LOCAL_RULE_ADAPTER_IMPORTS = {}


def local_rule_route_matches(key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    binding = CAPABILITY_SOURCES.resolve(key, target)
    source = CAPABILITY_SOURCES.source(target.get('providerId'))
    return (isinstance(binding, QueryBinding) and binding.key.capability in LOCAL_RULE_CAPABILITIES
            and source is not None and source.catalog_readiness is not None)


def local_rule_source_ready(key: Mapping[str, Any], target: Mapping[str, Any]) -> bool | None:
    if CAPABILITY_SOURCES.resolve(key, target) is None:
        return None
    source = CAPABILITY_SOURCES.source(target.get('providerId'))
    if source is None or source.catalog_readiness is None:
        return None
    result = source.catalog_readiness(CatalogReadinessContext(dict(key), dict(target)))
    if result is not None and type(result) is not bool:
        raise ValueError('来源目录就绪结果必须为bool或None')
    return result
