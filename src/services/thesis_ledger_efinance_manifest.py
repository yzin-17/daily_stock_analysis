"""Efinance 包装器的真实 EastMoney 能力登记。"""

from typing import Any

from src.services.provider_manifest import build_provider_manifest


def build_efinance_manifest() -> dict[str, Any]:
    capabilities = {
        "REALTIME_QUOTE": ("STOCK", "ETF"),
        "DAILY_BAR": ("STOCK", "ETF"),
        "FUND_NAV": ("MUTUAL_FUND",),
        "FUND_NAV_HISTORY": ("MUTUAL_FUND",),
    }
    manifest = build_provider_manifest(
        "efinance",
        "efinance",
        capabilities,
        upstream_sources=(("efinance", "efinance"), ("eastmoney", "东方财富")),
    )
    manifest["version"] = 2
    for source in manifest["upstreamSources"]:
        if source["sourceId"] == "eastmoney":
            source["capabilities"] = {
                capability: sorted(instrument_types)
                for capability, instrument_types in capabilities.items()
            }
    return manifest
