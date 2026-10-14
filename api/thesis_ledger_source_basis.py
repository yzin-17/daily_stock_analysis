"""V3原生价格事实的HTTP投影，只消费精确绑定的标准单位。"""

from typing import Any, Mapping

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import DailyBinding
from src.services.thesis_ledger_daily_units_contract import DailyFieldUnits, DailyUnitsContext


def native_field_units(attrs: Mapping[str, Any], key: Mapping[str, Any],
                       provider: str, source: str,
                       request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    names = {name for _, _, binding in CAPABILITY_SOURCES.routes('DAILY_BAR')
             if isinstance(binding, DailyBinding) and binding.public_units_policy is not None
             for name in binding.public_units_policy.metadata_names}
    present = {name for name in names if attrs.get(name) is not None}
    if not present:
        return {}
    binding = CAPABILITY_SOURCES.resolve(key, {'providerId': provider, 'upstreamSource': source})
    policy = binding.public_units_policy if isinstance(binding, DailyBinding) else None
    if policy is None or len(present) != 1 or not present.issubset(policy.metadata_names):
        raise ValueError('日线单位合同与精确来源绑定不匹配')
    metadata = {name: attrs[name] for name in present}
    result = policy.project(DailyUnitsContext(metadata, key, request))
    if not isinstance(result, DailyFieldUnits):
        raise ValueError('日线单位投影未返回标准结果')
    checked = DailyFieldUnits.model_validate(result.model_dump())
    return {'fieldUnits': checked.model_dump()}


def native_source_basis(adjustment: str, method_version: str, fingerprint: str,
                        fetched_at: str, units: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "adjustment": adjustment, "method": "provider-native",
        "methodVersion": method_version, "basisScope": "provider-defined",
        "anchor": None, "revision": {"origin": "local-observation", "contentHash": fingerprint},
        "observedAt": fetched_at, "volumeBasis": "unknown", **units,
        "dividendMeaning": "provider-defined", "dividendEvidenceRef": None,
        "conversionAvailable": False, "conversionEvidenceRef": None, "derivation": None,
    }
