"""Tushare CASH 生产门禁：原文先于账号，读取前后复核完整状态。"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot, _secret_key
from src.services.thesis_ledger_event_v3_adapters import EVENT_KEY, TUSHARE_TARGET, event_adapter_revisions
from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_tushare_mapped_read import read_tushare_mapped_fund_dividends
from src.services.tushare_fund_identity_evidence import _current_admission, _instant, resolve_tushare_fund_identity

TUSHARE_EVENT_TIMEOUT_SECONDS = 30


class TushareEventError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _local_admission(store):
    admission = deepcopy(store.get_route_admission_v3(key=EVENT_KEY, target=TUSHARE_TARGET))
    if (not isinstance(admission, dict) or admission.get("routeKey") != EVENT_KEY
            or admission.get("target") != TUSHARE_TARGET):
        raise ValueError("missing admission")
    revisions = event_adapter_revisions(EVENT_KEY, TUSHARE_TARGET, admission.get("credentialRevision"))
    if revisions is None or any(admission.get(field) != value for field, value in revisions.items()):
        raise ValueError("stale local revision")
    observed = datetime.now(timezone.utc)
    _current_admission(admission, observed_at=observed, cutoff=_instant(observed.isoformat()))
    return admission


def _identity_guard(store, request):
    """只读取请求范围的准入和原文件；此检查不读取账号或授予 ready。"""
    admission = _local_admission(store)
    database = store.database_path
    if not isinstance(database, str) or not database or database == ":memory:":
        raise ValueError("persistent evidence required")
    content = MappingEvidenceStore(
        Path(database).expanduser().resolve().parent / "thesis-ledger-mapping-evidence",
    ).read(admission.get("evidenceRef"))
    observed = datetime.now(timezone.utc)
    identity = resolve_tushare_fund_identity(
        content, admission, symbol=request["symbol"], start=request["start"], end=request["end"],
        data_as_of=request["dataAsOf"], observed_at=observed,
    )
    return admission, identity


def current_tushare_event_admission(store, key, target, manifest):
    if dict(key) != EVENT_KEY or dict(target) != TUSHARE_TARGET:
        return None
    try:
        admission = _local_admission(store)
        snapshot = store.provider_credential_snapshot("tushare")
        revision = _provider_credential_revision_from_snapshot(snapshot)
        if snapshot.source == "environment" and revision and _market_v3_admission_matches_current(
            admission, key, target, manifest, credential_revision=revision,
        ):
            return admission
    except Exception:
        return None
    return None


def _request_guard(store, request):
    try:
        return _identity_guard(store, request)
    except Exception:
        raise TushareEventError("not_admitted") from None


def _response(request, state, mapped, fetched):
    result, identity = mapped.result, mapped.identity
    revision, facts = result["providerRevision"], result["facts"]
    seen = set()
    for fact in facts:
        try:
            event = (fact["symbol"], fact["type"], fact["effectiveDate"])
            if (event in seen or fact["symbol"] != request["symbol"] or fact["market"] != "CN"
                    or fact["instrumentType"] != "ETF" or fact["type"] != "CASH_DIVIDEND"
                    or fact["provider"] != "tushare" or fact["providerRevision"] != revision
                    or fact["currency"] != identity.currency
                    or not request["start"] <= fact["effectiveDate"] <= request["end"]
                    or _instant(fact["availableAt"]) > min(_instant(request["dataAsOf"]), _instant(fetched))):
                raise ValueError()
            seen.add(event)
        except (KeyError, TypeError, ValueError):
            raise TushareEventError("invalid_response") from None
    admission = {name: value for name, value in state["admission"].items() if name != "recordedBy"}
    return {**request, "fetchedAt": fetched, "providerRevision": revision, "facts": facts,
            "admission": admission,
            "tushareIdentityEvidence": {"ref": identity.evidence_ref, "sha256": identity.evidence_sha256,
                                        "content": identity.content.decode("utf-8")},
            "coverage": {"complete": False, "reason": "historical_coverage_unverified"}}


def execute_tushare_events_v3(runtime, request, *, admitted_state, check_security=None):
    guard = _request_guard(runtime.store, request)
    if check_security:
        check_security()
    before = admitted_state(runtime, request)
    if before["admission"] != guard[0] or _request_guard(runtime.store, request) != guard:
        raise TushareEventError("policy_not_applied")
    if check_security:
        check_security()
    try:
        mapped = read_tushare_mapped_fund_dividends(
            request["symbol"], database_path=runtime.store.database_path,
            start=request["start"], end=request["end"], data_as_of=request["dataAsOf"],
            read_admission=lambda: runtime._current_market_v3_admission(EVENT_KEY, TUSHARE_TARGET),
            read_credentials=lambda: runtime.store.provider_credential_snapshot("tushare"),
            read_master_key=_secret_key,
            build_fetcher=lambda snapshot: runtime._adapter("tushare", snapshot=snapshot),
            timeout_seconds=TUSHARE_EVENT_TIMEOUT_SECONDS,
        )
    except Exception:
        raise TushareEventError("upstream_failure") from None
    if _request_guard(runtime.store, request) != guard or mapped.identity != guard[1]:
        raise TushareEventError("policy_not_applied")
    after = admitted_state(runtime, request)
    if after != before:
        raise TushareEventError("policy_not_applied")
    fetched = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    response = _response(request, after, mapped, fetched)
    if _request_guard(runtime.store, request) != guard:
        raise TushareEventError("policy_not_applied")
    if check_security:
        check_security()
    return response
