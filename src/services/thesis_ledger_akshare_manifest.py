"""AKShare 东财能力的精确 source 登记。"""

from typing import Any

from src.services.provider_manifest import build_provider_manifest


def build_akshare_manifest() -> dict[str, Any]:
    manifest = build_provider_manifest(
        "akshare",
        "AKShare",
        {
            "REALTIME_QUOTE": ("STOCK", "ETF"),
            "DAILY_BAR": ("STOCK", "ETF"),
            "FUND_NAV": ("MUTUAL_FUND",),
            "FUND_NAV_HISTORY": ("MUTUAL_FUND",),
            "FUND_HOLDINGS": ("MUTUAL_FUND",),
            "CHIP_SUMMARY": ("STOCK",),
        },
        upstream_sources=(
            ("akshare", "AKShare"),
            ("eastmoney", "东方财富"),
            ("sina", "新浪财经"),
            ("tencent", "腾讯财经"),
        ),
    )
    manifest["version"] = 3
    for source in manifest["upstreamSources"]:
        if source["sourceId"] == "eastmoney":
            source["capabilities"].update({
                "REALTIME_QUOTE": ["STOCK"],
                "FUND_NAV": ["MUTUAL_FUND"],
                "FUND_NAV_HISTORY": ["MUTUAL_FUND"],
                "FUND_HOLDINGS": ["MUTUAL_FUND"],
                "CHIP_SUMMARY": ["STOCK"],
            })
        elif source["sourceId"] in {"sina", "tencent"}:
            source["capabilities"]["REALTIME_QUOTE"] = ["STOCK"]
    return manifest
