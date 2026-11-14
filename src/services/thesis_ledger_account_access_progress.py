"""权限检查的限频暂停、部分接续与同源执行互斥。"""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

from src.services.thesis_ledger_control import ControlContractError
from src.services.thesis_ledger_account_access_groups import PermissionGroups

COOLDOWN_SECONDS = 60


def utc_now():
    return datetime.now(timezone.utc)


@contextmanager
def check_slot(runtime, provider_id):
    from src.services.thesis_ledger_account_access import _LOCK, _cache
    with _LOCK:
        active = _cache(runtime).setdefault("active", set())
        if provider_id in active:
            raise ControlContractError("PROVIDER_CHECK_IN_PROGRESS", "该来源正在检查，请等待本次检查结束", status_code=409)
        active.add(provider_id)
    try:
        yield
    finally:
        with _LOCK:
            active.discard(provider_id)


def previous_report(runtime, snapshot, level, *, draft, fixture):
    if fixture:
        return None
    from src.services.thesis_ledger_account_access_store import load_record
    if not draft:
        return load_record(runtime.store, snapshot, level)
    from src.services.thesis_ledger_account_access import _LOCK, _cache, TTL_SECONDS
    from src.services.thesis_ledger_account_access_configuration import draft_key
    with _LOCK:
        cached = _cache(runtime).setdefault("drafts", {}).get(draft_key(snapshot, level))
        if cached and cached[0] > runtime.clock():
            return deepcopy(cached[1])
    report = load_record(runtime.store, snapshot, level)
    if report:
        # 相同已保存输入也可在保存流程中复用；草稿响应仍保持隔离契约。
        report.update(credentialContext="draft", validUntil=(utc_now() + timedelta(seconds=TTL_SECONDS)).isoformat())
        report.pop("persisted", None)
        for route in report["routes"]:
            route["access"].pop("persisted", None)
            route["access"]["validUntil"] = report["validUntil"]
    return report


def cooling_down(report, seconds=COOLDOWN_SECONDS):
    if not report:
        return False
    for route in report["routes"]:
        access = route["access"]
        if access.get("errorCode") not in {"rate_limited", "rate_limited_global"} or not access.get("attempted"):
            continue
        try:
            checked_at = datetime.fromisoformat(access.get("checkedAt", report["checkedAt"]).replace("Z", "+00:00"))
            if checked_at + timedelta(seconds=seconds) > utc_now():
                return True
        except (ValueError, TypeError, KeyError):
            continue
    return False


def route_id(route):
    return json.dumps([route["key"], route["target"]], sort_keys=True)


def probe_routes(runtime, source, entries, level, previous, probe, check_current, *, deadline, ttl, config_version):
    partial = previous and any(r["access"]["state"] == "unknown" for r in previous["routes"])
    retained = {route_id(r): r["access"] for r in previous["routes"]
                if r["access"]["state"] != "unknown"} if partial else {}
    groups = PermissionGroups(source, previous, lambda report: cooling_down(report, source.account_access_cooldown_seconds))
    routes, requests, limited = [], 0, False
    for entry in entries:
        check_current()
        excluded = source.account_access_level.excluded(entry["key"], level) if source.account_access_level else None
        access = deepcopy(retained.get(route_id(entry)))
        if access is None and not excluded:
            access = groups.get(entry)
        if access is None:
            if excluded:
                access = dict(excluded)
            elif limited:
                access = {"state": "unknown", "attempted": False, "errorCode": "rate_limited"}
            else:
                remaining = deadline - runtime.clock() if requests < source.account_access_max_requests else 0
                access = probe(entry, remaining)
                requests += int(access.get("attempted", False))
                if groups.stops_source(access):
                    limited = True
                elif access.get("errorCode") not in {"rate_limited", "rate_limited_global"} and runtime.clock() > deadline:
                    access = {"state": "unknown", "attempted": access["attempted"], "errorCode": "request_budget_exceeded"}
            checked_at = utc_now()
            access.update(checkedAt=checked_at.isoformat(), validUntil=(checked_at + timedelta(seconds=ttl)).isoformat(),
                          configVersion=config_version)
            if not excluded and not limited or access.get("attempted"):
                groups.remember(entry, access)
        check_current()
        routes.append({"key": deepcopy(entry["key"]), "target": deepcopy(entry["target"]), "access": access})
    return routes


def check_response(source, provider, value, report, ready):
    included = [r for r in report["routes"] if r["access"]["state"] != "not_included"]
    status = "healthy" if included and all(r["access"]["state"] == "allowed" for r in included) else "degraded"
    if not ready and provider.get("requiresCredential", True):
        status = "unconfigured"
    return {"contractVersion": 3, "consumer": "thesis-ledger", "providerId": source.provider_id,
            "requestId": value["requestId"], "purpose": "account_access", "status": status,
            "credentialConfigured": ready, "capabilityResults": {}, "accountAccess": report}
