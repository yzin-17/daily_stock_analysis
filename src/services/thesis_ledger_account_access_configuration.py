"""配置账号前检查范围内权限，并将同一输入的记录保存到本地。"""

from copy import deepcopy
import json

from src.services.thesis_ledger_account_access_store import configured_level, configured_settings, write_record
from src.services.thesis_ledger_control import ControlContractError
from src.services.provider_credentials import validate_config_patch_keys
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot


def draft_key(snapshot, level):
    from src.services.thesis_ledger_account_access import _identity
    return _identity(snapshot), json.dumps(level, sort_keys=True)


def remember_draft(runtime, snapshot, level, report):
    from src.services.thesis_ledger_account_access import _cache, _LOCK, TTL_SECONDS
    with _LOCK:
        drafts = _cache(runtime).setdefault("drafts", {})
        now = runtime.clock()
        for key in list(drafts):
            if drafts[key][0] <= now:
                del drafts[key]
        if len(drafts) >= 16:
            del drafts[next(iter(drafts))]
        drafts[draft_key(snapshot, level)] = (now + TTL_SECONDS, deepcopy(report))


def configure_provider(store, provider_id, value, *, fixture=False):
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    try:
        validate_config_patch_keys(value)
    except ValueError as error:
        raise ControlContractError("INVALID_PROVIDER_CONFIG", str(error)) from error
    source = CAPABILITY_SOURCES.source(provider_id)
    if source and source.account_access_level and value.get("clearCredentials"):
        baseline = store.provider_credential_snapshot(provider_id)
        settings = {**configured_settings(store, provider_id), **value.get("settings", {})}
        settings.pop("accountLevel", None)
        return store.save_provider_config(provider_id, {**value, "settings": settings,
            "expectedConfigVersion": value.get("expectedConfigVersion", baseline.config_version)})
    if source is None or source.account_access_level is None or value.get("clearCredentials") or not (
            "credentials" in value or "accountLevel" in value.get("settings", {})):
        return store.save_provider_config(provider_id, value)
    from src.services.thesis_ledger_provider_runtime import get_thesis_ledger_runtime
    from src.services.thesis_ledger_provider_exact_checks import _snapshot, check_provider
    from src.services.thesis_ledger_account_access import _cache, _LOCK, _account_snapshot

    runtime = get_thesis_ledger_runtime()
    try:
        level = source.account_access_level.parse(value.get("settings", {}).get("accountLevel", configured_level(store, provider_id)))
    except ValueError as error:
        raise ControlContractError("INVALID_ACCOUNT_LEVEL", str(error)) from error
    if level is None:
        return store.save_provider_config(provider_id, value)
    provider = next(p for p in store.provider_registry() if p["providerId"] == provider_id)
    baseline, candidate = _snapshot(store, provider, value)
    if value.get("expectedConfigVersion", baseline.config_version) != baseline.config_version:
        raise ControlContractError("PROVIDER_CONFIG_VERSION_CONFLICT", "账号配置已变化，请重新读取", status_code=409)
    candidate = _account_snapshot(source, candidate)
    with _LOCK:
        cached = _cache(runtime).setdefault("drafts", {}).get(draft_key(candidate, level))
        report = deepcopy(cached[1]) if cached and cached[0] > runtime.clock() else None
    if report is None:
        check = {k: v for k, v in value.items() if k in {"contractVersion", "consumer", "requestId", "credentials"}}
        report = check_provider(runtime, provider_id, {**check, "purpose": "account_access", "accountLevel": level},
                                fixture=fixture)["accountAccess"]
    if any(r["access"]["state"] == "credential_invalid" for r in report["routes"]):
        raise ControlContractError("PROVIDER_CREDENTIAL_INVALID", "凭证无效，请检查后重新配置")
    if store.provider_credential_snapshot(provider_id) != baseline:
        raise ControlContractError("PROVIDER_CREDENTIAL_CHANGED", "检查期间账号配置发生变化")
    patch = {**value, "expectedConfigVersion": baseline.config_version,
             "settings": {**configured_settings(store, provider_id), **(value.get("settings") or {}), "accountLevel": level}}
    def persist(connection, config_version, credential_version):
        current = ProviderCredentialSnapshot.create(provider_id, candidate.source, candidate.method, dict(candidate.values),
                                                    config_version, credential_version)
        if _account_snapshot(source, current) != current:
            raise ControlContractError("PROVIDER_CREDENTIAL_CHANGED", "来源端点变化，权限记录未采用")
        if not fixture:
            write_record(connection, current, level, report)
    return store.save_provider_config(provider_id, patch, after_save=persist)
