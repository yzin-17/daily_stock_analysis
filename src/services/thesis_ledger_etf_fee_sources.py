"""现有官方规则Provider的三个精确ETF收费资料；不启用运行路由。"""

from pathlib import Path
from collections.abc import Mapping

BASE = Path(__file__).resolve().parents[2] / "data_provider/market_reference"
SOURCES = {
    "official-rules": ("CN", "cn-etf-handling-fee", "上交所ETF竞价经手费", "cn-510300-fees.json"),
    "official-hk-rules": ("HK", "hk-etf-public-fees", "港股ETF公共费用及印花税豁免", "hk-02800-public-fees.json"),
    "official-us-rules": ("US", "us-etf-public-fees", "美股ETF Section31及实际股数TAF", "us-spy-public-fees.json"),
}


def etf_fee_path(key, target):
    if (not isinstance(key, Mapping) or not isinstance(target, Mapping)
            or key.get("kind") != "data" or key.get("assetType") != "ETF"
            or key.get("capability") != "HISTORICAL_PUBLIC_FEES"):
        return None
    selected = SOURCES.get(target.get("providerId"))
    if selected and (key.get("market"), target.get("upstreamSource")) == selected[:2]:
        return BASE / selected[3]
    return None


def extend_manifest(manifest):
    from src.services.thesis_ledger_local_history_manifest import attach_local_history

    _, source, label, filename = SOURCES[manifest["providerId"]]
    manifest["capabilities"]["HISTORICAL_PUBLIC_FEES"] = ["ETF", "STOCK"]
    manifest["upstreamSources"].append({"sourceId": source, "displayName": label,
        "capabilities": {"HISTORICAL_PUBLIC_FEES": ["ETF"]}})
    return attach_local_history(manifest, {source: BASE / filename})


def bindings(provider):
    from data_provider.public_fee_projection import read_fees
    from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding
    from src.services.thesis_ledger_public_fees_contract_v1 import CONTRACT

    market, source, _, filename = SOURCES[provider]
    return (QueryBinding(CONTRACT, ExactKey(market, "ETF", "HISTORICAL_PUBLIC_FEES"), source,
        "bounded-etf-fees-adapter-v1", filename + ":v1", read_fees, admission_required=False),)
