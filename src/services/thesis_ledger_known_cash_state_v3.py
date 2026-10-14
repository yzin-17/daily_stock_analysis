"""已知现金研究的控制快照：所有消费者共享前后校验，凭证内容仅内部使用。"""

from copy import deepcopy
from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready

KEY = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"}


class KnownCashReadError(ValueError):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code, self.reason = code, reason


def capture_known_cash_control_v3(runtime, request):
    policy = runtime.store.effective_policy_v3()
    if (not isinstance(policy, dict) or policy.get("contractVersion") != 3 or policy.get("enabled") is not True
            or any(type(policy.get(field)) is not int for field in ("contractVersion", "revision", "sourceDesiredRevision"))
            or policy.get("revision") != request["effectivePolicyRevision"]
            or policy.get("sourceDesiredRevision") != request["desiredRevision"]):
        raise KnownCashReadError("policy_not_applied", "policy_unavailable")
    routes = [row for row in policy.get("routes", []) if isinstance(row, dict) and row.get("key") == KEY]
    target = request["routeTarget"]
    if len(routes) != 1 or not isinstance(routes[0].get("targets"), list):
        raise KnownCashReadError("not_admitted", "route_not_configured")
    targets = routes[0]["targets"]
    if (not 1 <= len(targets) <= 2 or any(not isinstance(row, dict) or type(row.get("routeIndex")) is not int
                                        or row["routeIndex"] != index for index, row in enumerate(targets))
            or target["routeIndex"] >= len(targets)
            or any(targets[target["routeIndex"]].get(field) != value for field, value in target.items())):
        raise KnownCashReadError("not_admitted", "route_not_configured")
    identities = []
    for row in targets:
        if any(not isinstance(row.get(name), str) or not row[name].strip()
               or row[name] != row[name].strip() for name in ("providerId", "upstreamSource")):
            raise KnownCashReadError("invalid_response", "invalid_control_or_source_response")
        identities.append((row["providerId"], row["upstreamSource"]))
    if len(set(identities)) != len(identities):
        raise KnownCashReadError("invalid_response", "invalid_control_or_source_response")
    selected = targets[target["routeIndex"]]
    if (type(selected.get("eligible")) is not bool
            or selected["eligible"] == (selected.get("reason") is not None)):
        raise KnownCashReadError("invalid_response", "invalid_control_or_source_response")
    blocked = {"credential_missing": "credential_missing", "dependency_missing": "dependency_missing",
               "quota_unavailable": "rate_limited", "disabled": "provider_unavailable"}
    if selected.get("reason") not in {None, "not_admitted", "admission_expired",
                                      "admission_not_yet_valid", "insufficient_coverage"}:
        raise KnownCashReadError("not_admitted", blocked.get(selected.get("reason"), "provider_unavailable"))
    catalog = runtime.market_route_catalog_v3()
    if (not isinstance(catalog, dict) or catalog.get("integrity") != "complete"
            or type(catalog.get("catalogRevision")) is not int
            or catalog.get("catalogRevision") != request["catalogRevision"]):
        raise KnownCashReadError("policy_not_applied", "catalog_version_mismatch")
    identity = {key: target[key] for key in ("providerId", "upstreamSource")}
    entries = [row for row in catalog.get("entries", []) if row.get("key") == KEY and row.get("target") == identity]
    if len(entries) != 1 or entries[0].get("state") not in {"ready", "not_admitted"}:
        raise KnownCashReadError("not_admitted", "provider_unavailable")
    providers = [row for row in runtime.store.provider_registry() if row.get("providerId") == target["providerId"]]
    if (len(providers) != 1 or providers[0].get("tombstone") is not None
            or providers[0].get("enabled") is not True or providers[0].get("configured") is not True
            or type(providers[0].get("requiresCredential")) is not bool):
        raise KnownCashReadError("not_admitted", "provider_unavailable")
    if not provider_dependency_ready(target["providerId"], KEY, identity):
        raise KnownCashReadError("not_adapted", "dependency_missing")
    snapshot = runtime.store.provider_credential_snapshot(target["providerId"])
    credential_revision = (_provider_credential_revision_from_snapshot(snapshot)
                           if providers[0]["requiresCredential"] else "not-required")
    if (not credential_revision or type(snapshot.config_version) is not int
            or snapshot.config_version < 0 or type(providers[0].get("configVersion")) is not int
            or providers[0].get("configVersion") != snapshot.config_version):
        raise KnownCashReadError("not_admitted", "credential_missing")
    return {"policy": deepcopy({key: value for key, value in policy.items() if key != "appliedAt"}),
            "catalog": deepcopy({key: catalog[key] for key in ("catalogRevision", "integrity", "entries")}),
            "provider": deepcopy({key: providers[0].get(key) for key in
                                  ("version", "enabled", "configured", "requiresCredential", "configVersion", "tombstone")}),
            "credential": snapshot, "credentialRevision": credential_revision}

