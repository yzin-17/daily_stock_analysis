"""基础价格配置与需独立审核能力的准入分界。"""

from src.services.thesis_ledger_market_v3_adapters import basic_market_price_route
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_local_rules import local_rule_route_matches
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from types import MappingProxyType
from src.services.thesis_ledger_capability_contracts import PolicyAdmissionContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import QueryBinding, EventBinding


def configured_data_route(key, target):
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if isinstance(binding, (QueryBinding, EventBinding)):
        if binding.admission_required is not None:
            return not binding.admission_required
        return not binding.gated
    return local_rule_route_matches(key, target) or current_data_adapter_revisions(key, target) is not None


def basic_price_credential_ready(snapshot):
    if snapshot is None:
        return False
    from src.services.provider_credentials import PROVIDER_CREDENTIAL_DEFINITIONS

    definitions = PROVIDER_CREDENTIAL_DEFINITIONS.get(snapshot.provider_id, ())
    definition = next((item for item in definitions if item.method == snapshot.method), None)
    return definition is not None and all(
        isinstance(snapshot.values.get(field), str) and bool(snapshot.values[field].strip())
        for field in definition.fields
    )


def basic_price_route_configured(store, key, target):
    if not basic_market_price_route(key, target) and not configured_data_route(key, target):
        return False
    from src.services.thesis_ledger_provider_manifests import PROVIDER_MANIFESTS
    manifest = PROVIDER_MANIFESTS.get(target["providerId"], {})
    if not manifest:
        return False
    from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready
    if not provider_dependency_ready(target["providerId"], key, target):
        return False
    if not manifest.get("requiresCredential"):
        return True
    from src.services.thesis_ledger_control import ControlContractError

    try:
        return basic_price_credential_ready(store.provider_credential_snapshot(target["providerId"]))
    except (AttributeError, ControlContractError, ValueError):
        return False


def price_route_admission(read_admission, request, key, target, *, credential_snapshot=None):
    if basic_market_price_route(key, target):
        return None, None
    admission = read_admission(key, target, credential_snapshot=credential_snapshot)
    if admission is None:
        return None, "NO_ELIGIBLE_PROVIDER"
    if not route_admission_scope_applies(
        dict(admission), symbol=request.symbol, date_from=request.start, date_to=request.end,
    ):
        return None, "insufficient_coverage"
    return admission, None


def policy_route_admission_reason(store, connection, key, target, consumer, manifest):
    """基础价格与本地历史规则按配置调用，其余能力保留既有状态。"""
    if basic_market_price_route(key, target) or configured_data_route(key, target):
        from src.services.thesis_ledger_provider_dependencies import provider_dependency_ready
        if not provider_dependency_ready(target["providerId"], key, target):
            return "dependency_missing"
        return None
    from src.services.thesis_ledger_event_v3_adapters import event_admission_reader

    provider_id = target["providerId"]
    identity = store._route_admission_identity_v3(key, target, consumer)
    row = connection.execute(
        """SELECT * FROM thesis_ledger_route_admission_v3
           WHERE consumer = ? AND route_key_json = ? AND provider_id = ? AND upstream_source = ?""", identity,
    ).fetchone()
    payload = store._route_admission_payload_v3(row) if row else None
    state = store._route_admission_state_v3(payload)
    if reader := event_admission_reader(key, target):
        if reader(store, key, target, manifest) is None:
            return 'not_admitted'
    source = CAPABILITY_SOURCES.source(provider_id)
    if source is not None and source.policy_admission_guard is not None:
        context = PolicyAdmissionContext(
            key=MappingProxyType(dict(key)), target=MappingProxyType(dict(target)),
            manifest=MappingProxyType(dict(manifest)), state=state,
            payload=MappingProxyType(dict(payload)) if payload is not None else None,
            read_credentials=lambda: store.provider_credential_snapshot(provider_id),
        )
        if source.policy_admission_guard(context) is not True:
            return "not_admitted"
    if state == "pending":
        return "not_admitted"
    return None if state == "admitted" else f"admission_{state}"
