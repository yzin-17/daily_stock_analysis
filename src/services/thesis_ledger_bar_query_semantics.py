"""可执行日线的标准目录事实；不证明算法等价或具体证券覆盖。"""

from .thesis_ledger_capability_sources import CAPABILITY_SOURCES
from .thesis_ledger_market_v3_facts import market_pagination_contract_v3, market_source_contract_v3


def bar_query_semantics(key, target):
    if key.get("kind") != "bar" or key.get("capability") != "DAILY_BAR" or key.get("timeframe") != "1d":
        return None
    binding = CAPABILITY_SOURCES.resolve(key, target)
    source_contract = market_source_contract_v3(key["assetType"], target["providerId"], target["upstreamSource"])
    # 未迁移条目仅消费现行专有整窗合同；其他来源保留原准入路径。
    legacy = source_contract is not None and key.get("assetType") == "ETF" and key.get("adjustment") == "qfq"
    if binding is None and not legacy:
        return None
    pagination = market_pagination_contract_v3(key["assetType"], target["providerId"], target["upstreamSource"])
    if pagination is None:
        return None
    return {"contractVersion": 3, "operation": "query", "adjustment": key["adjustment"],
            "priceBasis": "provider-native", "readScope": "single-target-whole-window",
            "coverageValidation": "per-request", "transportProtocol": pagination["protocol"]}
