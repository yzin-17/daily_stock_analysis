"""基础价格配置与需独立审核能力的准入分界。"""

from src.services.thesis_ledger_market_v3_adapters import basic_market_price_route
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies


def basic_price_credential_ready(snapshot):
    return (snapshot is not None and snapshot.method == "api_key"
            and isinstance(snapshot.values.get("apiKey"), str) and bool(snapshot.values["apiKey"].strip()))


def basic_price_route_configured(store, key, target):
    if not basic_market_price_route(key, target):
        return False
    if target["providerId"] == "tencent":
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
    """基础价格免人工准入；其余能力继续使用原有审核状态。"""
    if basic_market_price_route(key, target):
        return None
    from src.services.thesis_ledger_control import ControlContractError, _provider_credential_revision_from_snapshot
    from src.services.thesis_ledger_hithink_dividend_contract_v3 import hithink_dividend_route_matches
    from src.services.thesis_ledger_hithink_quote_admission import hithink_quote_revisions
    from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current

    provider_id, upstream_source = target["providerId"], target["upstreamSource"]
    identity = store._route_admission_identity_v3(key, target, consumer)
    row = connection.execute(
        """SELECT * FROM thesis_ledger_route_admission_v3
           WHERE consumer = ? AND route_key_json = ? AND provider_id = ? AND upstream_source = ?""", identity,
    ).fetchone()
    payload = store._route_admission_payload_v3(row) if row else None
    state = store._route_admission_state_v3(payload)
    if hithink_dividend_route_matches(key, target):
        from src.services.thesis_ledger_hithink_event_v3 import current_hithink_event_admission

        if current_hithink_event_admission(store, key, target, manifest) is None:
            return "not_admitted"
    if provider_id == "hithink":
        if state != "admitted":
            return "not_admitted"
        try:
            snapshot = store.provider_credential_snapshot(provider_id)
        except (ControlContractError, ValueError):
            return "not_admitted"
        revision = _provider_credential_revision_from_snapshot(snapshot)
        if key.get("kind") == "data" and key["capability"] == "REALTIME_QUOTE":
            versions = hithink_quote_revisions(key["assetType"], upstream_source)
            if (revision is None or versions is None
                    or payload.get("adapterRevision") != versions["adapterRevision"]
                    or payload.get("sourceRevision") != versions["sourceRevision"]
                    or payload.get("credentialRevision") != revision):
                return "not_admitted"
        elif revision is None or not _market_v3_admission_matches_current(
            {**payload, "admissionState": state}, key, target, manifest, credential_revision=revision,
        ):
            return "not_admitted"
    if state == "pending":
        return "not_admitted"
    return None if state == "admitted" else f"admission_{state}"
