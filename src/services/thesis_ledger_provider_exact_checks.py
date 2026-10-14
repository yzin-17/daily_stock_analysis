"""全来源精确诊断编排：库存、配置、当前证据和有界来源读取。"""

from copy import deepcopy
import time

from src.services.provider_credentials import CredentialValue, merge_credential_patch, parse_credential_patch
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_control import ControlContractError
from src.services.thesis_ledger_price_route_access import basic_price_credential_ready
from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready
from src.services.thesis_ledger_provider_runtime import classify_provider_exception
from src.services.thesis_ledger_exact_probe_read import read_exact_probe
from src.services.thesis_ledger_probe_execution import run_probe


def _check_baseline(runtime, baseline, catalog):
    if runtime.store.provider_credential_snapshot(baseline.provider_id) != baseline:
        raise ControlContractError("PROVIDER_CREDENTIAL_CHANGED", "检查期间凭证发生变化")
    current = runtime.market_route_catalog_v3()
    if current.get("catalogRevision") != catalog.get("catalogRevision") or current.get("integrity") != "complete":
        raise ControlContractError("ROUTE_CATALOG_CHANGED", "检查期间精确目录或准入状态发生变化")


def _snapshot(store, provider, value):
    baseline = store.provider_credential_snapshot(provider["providerId"])
    if "credentials" not in value:
        return baseline, baseline
    try:
        patch = parse_credential_patch(provider["providerId"], value["credentials"])
        previous = None
        if baseline.source == "control" and baseline.method == patch.method and baseline.values:
            previous = CredentialValue(baseline.method, dict(baseline.values))
        merged = merge_credential_patch(provider["providerId"], patch, previous)
    except ValueError as exc:
        raise ControlContractError("INVALID_PROVIDER_CREDENTIALS", str(exc), request_id=value["requestId"]) from None
    return baseline, ProviderCredentialSnapshot.create(provider["providerId"], "control", merged.method,
        merged.values, baseline.config_version, baseline.credential_version)


def check_provider(runtime, provider_id, value, *, fixture=False):
    unknown = set(value) - {"contractVersion", "consumer", "requestId", "credentials", "route", "purpose"}
    if unknown or value.get("purpose", "capability_read") not in {"account_access", "capability_read"}:
        raise ControlContractError("INVALID_PROVIDER_TEST", "来源检查请求包含未知字段", request_id=value["requestId"])
    provider = next((p for p in runtime.store.provider_registry() if p["providerId"] == provider_id.strip().lower()), None)
    if provider is None:
        raise ControlContractError("UNKNOWN_PROVIDER", "数据源不存在", request_id=value["requestId"])
    catalog = runtime.market_route_catalog_v3()
    if catalog.get("integrity") != "complete":
        raise ControlContractError("ROUTE_CATALOG_UNAVAILABLE", "精确目录尚未完整", request_id=value["requestId"])
    if value.get("purpose") == "account_access":
        from src.services.thesis_ledger_account_access import check_account_access
        return check_account_access(runtime, provider, value, catalog, fixture=fixture)
    entries = [deepcopy(e) for e in catalog["entries"] if e["target"]["providerId"] == provider["providerId"]]
    selected = value.get("route")
    if selected is not None:
        if not isinstance(selected, dict) or set(selected) != {"key", "target"}:
            raise ControlContractError("INVALID_PROVIDER_TEST", "检查必须指定精确能力和上游来源", request_id=value["requestId"])
        chosen = [e for e in entries if e["key"] == selected["key"] and e["target"] == selected["target"]]
        if len(chosen) != 1:
            raise ControlContractError("INVALID_PROVIDER_TEST", "该精确能力不属于当前数据源", request_id=value["requestId"])
    else:
        representatives = {}
        for entry in entries:
            representatives.setdefault(entry["key"]["capability"], entry)
        chosen = list(representatives.values())
    baseline, snapshot = _snapshot(runtime.store, provider, value)
    credential_ready = not provider["requiresCredential"] or basic_price_credential_ready(snapshot)
    routes, capabilities = [], {}
    for entry in entries:
        key, target = entry["key"], entry["target"]
        result = {"status": "unavailable", "readOnly": True, "attempted": False, "errorCode": "not_tested"}
        if entry in chosen:
            if not credential_ready:
                result.update(status="unconfigured", errorCode="credential_missing")
            elif not provider_dependency_ready(provider["providerId"], key, target):
                result["errorCode"] = "dependency_missing"
            elif fixture:
                result["errorCode"] = "fixture_mode"
            else:
                started = time.monotonic()
                attempted = False
                try:
                    _check_baseline(runtime, baseline, catalog)
                    # 独立事件证据在精确读取器中检查；普通读取不要求人工记录。
                    attempted = True
                    sample = run_probe(lambda probe_key=key, probe_target=target, credential=snapshot:
                                       read_exact_probe(runtime, probe_key, probe_target, credential))
                    _check_baseline(runtime, baseline, catalog)
                    result = {"status": "healthy", "readOnly": True, "attempted": True,
                              "latencyMs": max(0, int((time.monotonic() - started) * 1000))}
                    entry["sample"] = sample
                except Exception as exc:
                    code = getattr(exc, "code", None) or classify_provider_exception(exc)
                    result["errorCode"] = code
                    result["attempted"] = attempted and code not in {"not_admitted", "credential_missing", "not_adapted"}
            capabilities[key["capability"]] = result
        routes.append({"key": key, "target": target, "state": entry["state"], "result": result,
                       **({"sample": entry["sample"]} if "sample" in entry else {})})
    tested = list(capabilities.values())
    status = "degraded"
    if not credential_ready:
        status = "unconfigured"
    elif tested and all(r["status"] == "healthy" and r["attempted"] for r in tested):
        status = "healthy"
    return {"contractVersion": 3, "consumer": "thesis-ledger", "providerId": provider["providerId"],
            "requestId": value["requestId"], "status": status, "credentialConfigured": credential_ready,
            "capabilityResults": capabilities, "routeResults": routes}
