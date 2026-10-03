"""Provider 静态能力与只写凭据的公共 manifest 构造。"""

from typing import Any, Iterable

from src.services.provider_credentials import credential_schema


def build_provider_manifest(
    provider_id: str,
    display_name: str,
    capabilities: dict[str, Iterable[str]],
    *,
    requires_credential: bool = False,
    upstream_sources: Iterable[tuple[str, str]] = (),
    markets: Iterable[str] = ("CN",),
    configuration_mode: str = "control",
) -> dict[str, Any]:
    declared_sources = tuple(upstream_sources) or ((provider_id, display_name),)
    source_capabilities = {}
    for source_id, _ in declared_sources:
        # 非 direct source 目前只有 DAILY_BAR 有显式 source dispatch；其余
        # capability 不得借用 provider-wide 声明冒充 source 可执行能力。
        declared_capabilities = capabilities if source_id == provider_id else {
            "DAILY_BAR": capabilities.get("DAILY_BAR", ())
        }
        source_capabilities[source_id] = {
            capability: sorted(
                set(instrument_types)
                - ({"ETF"} if provider_id == "akshare" and source_id == "sina" and capability == "DAILY_BAR" else set())
            )
            for capability, instrument_types in declared_capabilities.items()
        }
    return {
        "providerId": provider_id,
        "displayName": display_name,
        "version": 1,
        "origin": "dsa",
        "markets": sorted(set(markets)),
        "configurationMode": configuration_mode,
        "upstreamSources": [
            {"sourceId": source_id, "displayName": source_name, "capabilities": source_capabilities[source_id]}
            for source_id, source_name in declared_sources
        ],
        "capabilities": {key: sorted(set(value)) for key, value in capabilities.items()},
        "requiresCredential": requires_credential,
        "configSchema": {
            "credential": {"writeOnly": True, "required": requires_credential},
            "settings": {},
        },
        "credentialSchema": credential_schema(provider_id),
    }
