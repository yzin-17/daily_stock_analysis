"""精确 RouteTarget 能力目录的投影与内容修订。"""

import hashlib
import json
from typing import Any, Callable, Mapping
from datetime import datetime, timezone
from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
from src.services.thesis_ledger_catalog_manifest_v3 import catalog_manifest_matches_v3
from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready
from src.services.thesis_ledger_bar_query_semantics import bar_query_semantics
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_event_catalog_v3 import catalog_event_admissions


def build_market_route_catalog_v3(provider_registry: Any, is_current: Callable[..., bool],
                                  *, current_admission=None, event_admissions=False, account_access=None) -> dict[str, Any]:
    """Return exact source-pinned V3 routes and current provider admission."""
    integrity = "complete"
    registry = provider_registry
    if not isinstance(registry, list):
        registry = []
        integrity = "partial"

    providers: dict[str, Mapping[str, Any]] = {}
    ambiguous_providers: set[str] = set()
    for item in registry:
        if not isinstance(item, Mapping):
            integrity = "partial"
            continue
        provider_value = item.get("providerId")
        provider_id = provider_value.strip().lower() if isinstance(provider_value, str) else ""
        if not provider_id:
            integrity = "partial"
            continue
        if provider_id in providers:
            ambiguous_providers.add(provider_id)
            integrity = "partial"
            continue
        providers[provider_id] = item

    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key, target, gated in exact_route_inventory():
        identity = json.dumps(
            [key, target], ensure_ascii=True, sort_keys=True, separators=(",", ":")
        )
        if identity in seen:
            integrity = "partial"
            entries = []
            break
        seen.add(identity)

        provider_id = target["providerId"]
        provider = providers.get(provider_id)
        if gated and provider is None:
            continue
        state = "not_admitted"
        if provider is None or provider_id in ambiguous_providers:
            integrity = "partial"
        elif not catalog_manifest_matches_v3(provider, key, target):
            # A broad manifest drifted away from the concrete adapter inventory.
            integrity = "partial"
        elif provider.get("tombstone") is not None:
            state = "not_admitted"
        elif provider.get("requiresCredential") is True and provider.get("credentialConfigured") is False:
            state = "credential_missing"
        elif provider.get("enabled") is False:
            state = "not_admitted"
        elif provider.get("enabled") is not True:
            integrity = "partial"
        elif not provider_dependency_ready(provider_id, key, target):
            state = "dependency_missing"
        elif provider.get("configured") is False:
            state = "credential_missing" if provider.get("requiresCredential") else "not_admitted"
        elif provider.get("configured") is not True:
            integrity = "partial"
        elif provider.get("requiresCredential") is True:
            credential_configured = provider.get("credentialConfigured")
            if not isinstance(credential_configured, bool):
                integrity = "partial"
            elif credential_configured:
                state = (
                    "ready"
                    if is_current(key, target, provider)
                    else "not_admitted"
                )
            else:
                state = "credential_missing"
        elif provider.get("requiresCredential") is False:
            if is_current(key, target, provider):
                # Ready means an exact, current admission exists for its
                # recorded scope. Data V3 checks that scope on every request.
                state = "ready"
            else:
                state = "not_admitted"
        else:
            integrity = "partial"

        semantics = bar_query_semantics(key, target)
        binding = CAPABILITY_SOURCES.resolve(key, target)
        support = getattr(binding, "query_support", None)
        access = account_access(key, target) if account_access is not None else None
        entries.append({"key": key, "target": target, "state": state,
                        **({"accountAccess": access} if access is not None else {}),
                        **({"querySupport": support.model_dump()} if support is not None else {}),
                        **({"barQuery": semantics} if semantics is not None else {})})

    admissions = catalog_event_admissions(entries, current_admission)
    revision_state = {"integrity": integrity, "entries": entries}
    if admissions:
        revision_state['admissions'] = admissions
    revision_input = json.dumps(
        revision_state,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    catalog_revision = int(hashlib.sha256(revision_input).hexdigest()[:13], 16) or 1
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    catalog = {
        "contractVersion": 3,
        "consumer": "thesis-ledger",
        "catalogRevision": catalog_revision,
        "generatedAt": generated_at,
        "integrity": integrity,
        "entries": entries,
    }
    if event_admissions:
        return {'protocolVersion': 'event-admission-catalog-v1', 'catalog': catalog, 'admissions': admissions}
    return catalog


def build_runtime_route_catalog(runtime, *, event_admissions=False):
    from src.services.thesis_ledger_account_access import account_access_for
    return build_market_route_catalog_v3(
        runtime.store.provider_registry(), runtime._catalog_market_v3_admission_is_current,
        current_admission=runtime._current_market_v3_admission, event_admissions=event_admissions,
        account_access=lambda key, target: account_access_for(runtime, key, target))
