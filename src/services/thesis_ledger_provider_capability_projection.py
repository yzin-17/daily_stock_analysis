"""来源清单按应用精确库存显示能力，保留静态凭证与标签定义。"""

from copy import deepcopy
from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory


def exact_provider_manifests(manifests):
    projected = deepcopy(manifests)
    scopes = {}
    for key, target, _gated in exact_route_inventory():
        scopes.setdefault(target["providerId"], []).append((key, target))
    for provider_id, manifest in projected.items():
        entries = scopes.get(provider_id, [])
        capabilities, sources, markets = {}, {}, set()
        labels = {s["sourceId"]: s for s in manifest.get("upstreamSources", [])}
        for key, target in entries:
            markets.add(key["market"])
            capabilities.setdefault(key["capability"], set()).add(key["assetType"])
            sources.setdefault(target["upstreamSource"], {}).setdefault(key["capability"], set()).add(key["assetType"])
        manifest["capabilities"] = {cap: sorted(assets) for cap, assets in sorted(capabilities.items())}
        manifest["markets"] = sorted(markets)
        manifest["upstreamSources"] = [
            {**labels.get(source, {"sourceId": source, "displayName": manifest["displayName"]}),
             "capabilities": {cap: sorted(assets) for cap, assets in sorted(caps.items())}}
            for source, caps in sorted(sources.items())
        ]
    return projected
