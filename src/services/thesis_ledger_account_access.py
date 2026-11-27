"""有界账户检查、草稿缓存及当前账号本地权限投影。"""

from copy import deepcopy
import hmac
import json
from secrets import token_bytes
from datetime import timedelta
from threading import RLock
from types import MappingProxyType
from uuid import uuid4
from weakref import WeakKeyDictionary

from src.services.thesis_ledger_account_access_contract import AccountAccessProbeContext, AccountAccessResult
from src.services.thesis_ledger_account_access_progress import check_slot, previous_report, cooling_down, probe_routes, check_response, public_access, utc_now
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import ControlContractError
from src.services.thesis_ledger_price_route_access import basic_price_credential_ready
from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready
from src.services.thesis_ledger_route_credentials import route_requires_credential

_LOCK = RLock()
_CACHES = WeakKeyDictionary()
TTL_SECONDS = 300
REQUEST_BUDGET_SECONDS = 18
MAX_ROUTE_ENTRIES = 64
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


def credential_required(source, key, target):
    return route_requires_credential(key, target, source.manifest())


def account_access_for(runtime, key, target):
    source = CAPABILITY_SOURCES.source(target["providerId"])
    if source is None or source.account_access_probe is None:
        return None
    if source.account_access_scope is not None and not source.account_access_scope(key):
        return None
    read_snapshot = getattr(runtime.store, "provider_credential_snapshot", None)
    if not callable(read_snapshot):
        return {"state": "unknown", "attempted": False, "errorCode": "permission_not_checked"}
    try:
        snapshot = _account_snapshot(source, read_snapshot(target["providerId"]))
    except (ControlContractError, ValueError):
        return {"state": "unknown", "attempted": False, "errorCode": "credential_unavailable"}
    if credential_required(source, key, target) and not basic_price_credential_ready(snapshot):
        return {"state": "credential_missing", "attempted": False, "errorCode": "credential_missing"}
    identity = _identity(snapshot)
    from src.services.thesis_ledger_account_access_store import configured_level, load_record
    try:
        stored = load_record(runtime.store, snapshot, configured_level(runtime.store, source.provider_id))
    except (ControlContractError, ValueError):
        return {"state": "unknown", "attempted": False, "errorCode": "account_level_invalid"}
    if stored:
        for route in stored["routes"]:
            if route["key"] == key and route["target"] == target:
                return public_access(route["access"])
    with _LOCK:
        cached = _cache(runtime)["reports"].get(target["providerId"])
        if cached and cached[0] == identity and runtime.clock() < cached[1]:
            for route in cached[2]["routes"]:
                if route["key"] == key and route["target"] == target:
                    return {**public_access(route["access"]), "checkedAt": cached[2]["checkedAt"],
                            "validUntil": cached[2]["validUntil"]}
        if cached and runtime.clock() >= cached[1]:
            _cache(runtime)["reports"][target["providerId"]] = (cached[0], cached[1], None)
    code = "permission_check_expired" if cached and cached[0] == identity else "permission_not_checked"
    return {"state": "unknown", "attempted": False, "errorCode": code}


def _check_one(runtime, source, entry, snapshot, adapter, remaining, previous_access, *, fixture):
    if credential_required(source, entry["key"], entry["target"]) and not basic_price_credential_ready(snapshot):
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
                                            snapshot, adapter, min(3, remaining), previous_access)
        value = run_probe(lambda: source.account_access_probe(context), timeout_seconds=context.timeout_seconds)
        return AccountAccessResult.model_validate(value).model_dump(exclude_none=True)
    except Exception as error:
        code = getattr(error, "code", "invalid_response")
        return {"state": "unknown", "attempted": code != "probe_busy", "errorCode": code}


def check_account_access(runtime, provider, value, catalog, *, fixture=False):
    with check_slot(runtime, provider["providerId"]):
        return _check_account_access(runtime, provider, value, catalog, fixture=fixture)


def _check_account_access(runtime, provider, value, catalog, *, fixture=False):
    from src.services.thesis_ledger_provider_exact_checks import _check_baseline, _snapshot

    source = CAPABILITY_SOURCES.source(provider["providerId"])
    if source is None or source.account_access_probe is None or "route" in value:
        raise ControlContractError("INVALID_PROVIDER_TEST", "该来源未声明账户检查或请求指定了业务读取目标")
    entries = [e for e in catalog["entries"] if e["target"]["providerId"] == source.provider_id
               and (source.account_access_scope is None or source.account_access_scope(e["key"]))]
    if not entries or len(entries) > MAX_ROUTE_ENTRIES:
        raise ControlContractError("INVALID_PROVIDER_TEST", "账户检查库存超出有界请求数量")
    baseline, snapshot = _snapshot(runtime.store, provider, value)
    normalized_baseline, snapshot = _account_snapshot(source, baseline), _account_snapshot(source, snapshot)
    from src.services.thesis_ledger_account_access_store import configured_level, save_record
    policy = source.account_access_level
    try:
        level = policy.parse(value["accountLevel"]) if policy and "accountLevel" in value else configured_level(runtime.store, source.provider_id)
    except ValueError as error:
        raise ControlContractError("INVALID_ACCOUNT_LEVEL", str(error)) from error
    def check_current():
        _check_baseline(runtime, baseline, catalog)
        current = _account_snapshot(source, runtime.store.provider_credential_snapshot(source.provider_id))
        if current != normalized_baseline:
            raise ControlContractError("PROVIDER_CREDENTIAL_CHANGED", "检查期间凭证或来源端点发生变化")
    draft = "credentials" in value or "accountLevel" in value
    check_current()
    previous = previous_report(runtime, snapshot, level, draft=draft, fixture=fixture)
    ready = basic_price_credential_ready(snapshot)
    global_limit = previous and any(r["access"].get("errorCode") == "rate_limited_global" for r in previous["routes"])
    if (source.account_access_group is None or global_limit) and cooling_down(previous, source.account_access_cooldown_seconds):
        check_current()
        return check_response(source, provider, value, previous, ready)
    generation = str(uuid4())
    with _LOCK:
        _cache(runtime)["generations"][source.provider_id] = generation
    deadline = runtime.clock() + REQUEST_BUDGET_SECONDS
    adapter = None
    if (ready or not provider.get("requiresCredential", True)) and not fixture and provider_dependency_ready(source.provider_id, entries[0]["key"], entries[0]["target"]):
        check_current()
        adapter = runtime._adapter(source.provider_id, snapshot=snapshot, cache=False, probe=True,
                                   market_v3_route_key=entries[0]["key"])
    routes = probe_routes(runtime, source, entries, level, previous,
                          lambda entry, remaining, prior: _check_one(
                              runtime, source, entry, snapshot, adapter, remaining, prior, fixture=fixture),
                          check_current, deadline=deadline, ttl=TTL_SECONDS, config_version=baseline.config_version)
    checked_at = utc_now()
    valid_until = (checked_at + timedelta(seconds=TTL_SECONDS)).isoformat()
    report = {"scope": "account_interfaces", "credentialContext": "draft" if draft else "current",
              "configVersion": baseline.config_version, "checkedAt": checked_at.isoformat(),
              "validUntil": valid_until, "routes": routes}
    if level is not None:
        report["accountLevel"] = level
    if draft and not fixture:
        from src.services.thesis_ledger_account_access_configuration import remember_draft
        remember_draft(runtime, snapshot, level, report)
    if not draft and not fixture:
        with _LOCK:
            if _cache(runtime)["generations"].get(source.provider_id) != generation:
                raise ControlContractError("PROVIDER_CHECK_SUPERSEDED", "已有更新的账户检查，不采用迟到结果")
            check_current()
            _cache(runtime)["reports"][source.provider_id] = (_identity(snapshot), runtime.clock() + TTL_SECONDS, deepcopy(report))
            report = save_record(runtime.store, snapshot, level, report)
    return check_response(source, provider, value, report, ready)
