"""有界账户检查和短期显示缓存；不持久化凭证或改变业务路由。"""

from copy import deepcopy
import hmac
import json
from secrets import token_bytes
from datetime import datetime, timedelta, timezone
from threading import RLock
from types import MappingProxyType
from uuid import uuid4
from weakref import WeakKeyDictionary

from src.services.thesis_ledger_account_access_contract import AccountAccessProbeContext, AccountAccessResult
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import ControlContractError
from src.services.thesis_ledger_price_route_access import basic_price_credential_ready
from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready

_LOCK = RLock()
_CACHES = WeakKeyDictionary()
TTL_SECONDS = 300
REQUEST_BUDGET_SECONDS = 18
MAX_REQUESTS = 6
_IDENTITY_KEY = token_bytes(32)


def _cache(runtime):
    with _LOCK:
        return _CACHES.setdefault(runtime, {"generations": {}, "reports": {}})


def _account_snapshot(source, snapshot):
    return source.account_access_snapshot(snapshot) if source.account_access_snapshot is not None else snapshot


def _identity(snapshot):
    payload = json.dumps([snapshot.provider_id, snapshot.source, snapshot.method, dict(snapshot.values),
                          snapshot.config_version, snapshot.credential_version], sort_keys=True).encode()
    return hmac.digest(_IDENTITY_KEY, payload, "sha256")


def account_access_for(runtime, key, target):
    source = CAPABILITY_SOURCES.source(target["providerId"])
    if source is None or source.account_access_probe is None:
        return None
    read_snapshot = getattr(runtime.store, "provider_credential_snapshot", None)
    if not callable(read_snapshot):
        return {"state": "unknown", "attempted": False, "errorCode": "permission_not_checked"}
    try:
        snapshot = _account_snapshot(source, read_snapshot(target["providerId"]))
    except (ControlContractError, ValueError):
        return {"state": "unknown", "attempted": False, "errorCode": "credential_unavailable"}
    if not basic_price_credential_ready(snapshot):
        return {"state": "credential_missing", "attempted": False, "errorCode": "credential_missing"}
    identity = _identity(snapshot)
    with _LOCK:
        cached = _cache(runtime)["reports"].get(target["providerId"])
        if cached and cached[0] == identity and runtime.clock() < cached[1]:
            for route in cached[2]["routes"]:
                if route["key"] == key and route["target"] == target:
                    return {**deepcopy(route["access"]), "checkedAt": cached[2]["checkedAt"],
                            "validUntil": cached[2]["validUntil"]}
        if cached and runtime.clock() >= cached[1]:
            _cache(runtime)["reports"][target["providerId"]] = (cached[0], cached[1], None)
    code = "permission_check_expired" if cached and cached[0] == identity else "permission_not_checked"
    return {"state": "unknown", "attempted": False, "errorCode": code}


def _check_one(runtime, source, entry, snapshot, adapter, remaining, *, fixture):
    if not basic_price_credential_ready(snapshot):
        return {"state": "credential_missing", "attempted": False, "errorCode": "credential_missing"}
    if fixture:
        return {"state": "unknown", "attempted": False, "errorCode": "fixture_mode"}
    if not provider_dependency_ready(source.provider_id, entry["key"], entry["target"]):
        return {"state": "unknown", "attempted": False, "errorCode": "dependency_missing"}
    if remaining <= 0:
        return {"state": "unknown", "attempted": False, "errorCode": "request_budget_exceeded"}
    from src.services.thesis_ledger_probe_execution import run_probe
    try:
        context = AccountAccessProbeContext(MappingProxyType(entry["key"]), MappingProxyType(entry["target"]),
                                            snapshot, adapter, min(3, remaining))
        value = run_probe(lambda: source.account_access_probe(context), timeout_seconds=context.timeout_seconds)
        return AccountAccessResult.model_validate(value).model_dump(exclude_none=True)
    except Exception as error:
        code = getattr(error, "code", "invalid_response")
        return {"state": "unknown", "attempted": code != "probe_busy", "errorCode": code}


def check_account_access(runtime, provider, value, catalog, *, fixture=False):
    from src.services.thesis_ledger_provider_exact_checks import _check_baseline, _snapshot

    source = CAPABILITY_SOURCES.source(provider["providerId"])
    if source is None or source.account_access_probe is None or "route" in value:
        raise ControlContractError("INVALID_PROVIDER_TEST", "该来源未声明账户检查或请求指定了业务读取目标")
    entries = [e for e in catalog["entries"] if e["target"]["providerId"] == source.provider_id]
    if not entries or len(entries) > MAX_REQUESTS:
        raise ControlContractError("INVALID_PROVIDER_TEST", "账户检查库存超出有界请求数量")
    baseline, snapshot = _snapshot(runtime.store, provider, value)
    normalized_baseline, snapshot = _account_snapshot(source, baseline), _account_snapshot(source, snapshot)
    def check_current():
        _check_baseline(runtime, baseline, catalog)
        current = _account_snapshot(source, runtime.store.provider_credential_snapshot(source.provider_id))
        if current != normalized_baseline:
            raise ControlContractError("PROVIDER_CREDENTIAL_CHANGED", "检查期间凭证或来源端点发生变化")
    generation = str(uuid4())
    with _LOCK:
        _cache(runtime)["generations"][source.provider_id] = generation
    deadline = runtime.clock() + REQUEST_BUDGET_SECONDS
    adapter = None
    ready = basic_price_credential_ready(snapshot)
    if ready and not fixture and provider_dependency_ready(source.provider_id, entries[0]["key"], entries[0]["target"]):
        check_current()
        adapter = runtime._adapter(source.provider_id, snapshot=snapshot, cache=False, probe=True,
                                   market_v3_route_key=entries[0]["key"])
    routes = []
    for entry in entries:
        check_current()
        access = _check_one(runtime, source, entry, snapshot, adapter, deadline - runtime.clock(), fixture=fixture)
        if runtime.clock() > deadline:
            access = {"state": "unknown", "attempted": access["attempted"], "errorCode": "request_budget_exceeded"}
        check_current()
        routes.append({"key": deepcopy(entry["key"]), "target": deepcopy(entry["target"]), "access": access})
    checked_at = datetime.now(timezone.utc)
    valid_until = (checked_at + timedelta(seconds=TTL_SECONDS)).isoformat()
    for route in routes:
        route["access"].update(checkedAt=checked_at.isoformat(), validUntil=valid_until, configVersion=baseline.config_version)
    report = {"scope": "account_interfaces", "credentialContext": "draft" if "credentials" in value else "current",
              "configVersion": baseline.config_version, "checkedAt": checked_at.isoformat(),
              "validUntil": valid_until, "routes": routes}
    if "credentials" not in value and not fixture:
        with _LOCK:
            if _cache(runtime)["generations"].get(source.provider_id) != generation:
                raise ControlContractError("PROVIDER_CHECK_SUPERSEDED", "已有更新的账户检查，不采用迟到结果")
            check_current()
            _cache(runtime)["reports"][source.provider_id] = (_identity(snapshot), runtime.clock() + TTL_SECONDS, deepcopy(report))
    status = "healthy" if all(r["access"]["state"] == "allowed" for r in routes) else "degraded"
    if not ready:
        status = "unconfigured"
    return {"contractVersion": 3, "consumer": "thesis-ledger", "providerId": source.provider_id,
            "requestId": value["requestId"], "purpose": "account_access", "status": status,
            "credentialConfigured": ready, "capabilityResults": {}, "accountAccess": report}
