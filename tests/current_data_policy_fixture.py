"""Current exact data policy fixture for runtime and gateway tests."""

from datetime import datetime, timedelta, timezone

from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, ThesisLedgerControlStore
from src.services.thesis_ledger_current_data_route import iter_current_data_adapters
from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions


def apply_data_policy(
    store: ThesisLedgerControlStore,
    routes: dict[str, dict[str, list[str]]],
    *,
    revision: int = 1,
) -> None:
    now = datetime.now(timezone.utc)
    targets = []
    for capability, asset_routes in routes.items():
        for asset_type, provider_ids in asset_routes.items():
            key = {
                "kind": "data", "market": "CN", "assetType": asset_type,
                "capability": capability,
            }
            route_targets = []
            for provider_id in provider_ids:
                exact_sources = [
                    target["upstreamSource"] for adapter_key, target in iter_current_data_adapters()
                    if adapter_key == key and target["providerId"] == provider_id
                ]
                source = exact_sources[0] if exact_sources else next(
                    item["sourceId"] for item in PROVIDER_MANIFESTS[provider_id]["upstreamSources"]
                    if asset_type in item.get("capabilities", {}).get(capability, [])
                )
                target = {"providerId": provider_id, "upstreamSource": source}
                revisions = market_v3_current_route_revisions(
                    key, target, PROVIDER_MANIFESTS[provider_id],
                ) or {
                    "adapterRevision": "fixture-adapter",
                    "sourceRevision": "fixture-source",
                    "credentialRevision": "fixture-built-in",
                }
                store.record_route_admission_v3(
                    key=key, target=target,
                    evidence_ref="fixture://current-data-route",
                    evidence_sha256="a" * 64,
                    scope_symbols=["600519.SH", "510300.SH", "159516.SZ", "000001.OF"],
                    scope_date_from="2024-01-01", scope_date_to="2027-12-31",
                    adapter_revision=revisions["adapterRevision"],
                    source_revision=revisions["sourceRevision"],
                    credential_revision=revisions["credentialRevision"],
                    valid_from=(now - timedelta(days=1)).isoformat(),
                    valid_until=(now + timedelta(days=1)).isoformat(),
                    recorded_by="pytest-fixture",
                )
                route_targets.append(target)
            targets.append({"key": key, "targets": route_targets})
    store.apply_policy_v3({
        "contractVersion": 3, "consumer": "thesis-ledger",
        "requestId": f"fixture-policy-{revision}", "revision": revision,
        "enabled": True, "routes": targets,
    })
