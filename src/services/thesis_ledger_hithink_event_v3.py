"""HiThink ETF 分红 V3：证据先行，固定来源，读前读后复核。"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.services.hithink_fund_identity_evidence import _instant, resolve_hithink_fund_identity
from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot, _secret_key
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, hithink_dividend_route_revisions,
)
from src.services.thesis_ledger_hithink_mapped_dividend_read import read_hithink_mapped_fund_dividends
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current


HITHINK_EVENT_TIMEOUT_SECONDS = 30


class HiThinkEventError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _local_admission(store):
    admission = deepcopy(store.get_route_admission_v3(key=HITHINK_DIVIDEND_KEY, target=HITHINK_DIVIDEND_TARGET))
    if (not isinstance(admission, dict) or admission.get("routeKey") != HITHINK_DIVIDEND_KEY
            or admission.get("target") != HITHINK_DIVIDEND_TARGET
            or admission.get("admissionState") != "admitted"):
        raise ValueError("missing admission")
    revisions = hithink_dividend_route_revisions(
        HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, admission.get("credentialRevision"),
    )
    if revisions is None or any(admission.get(field) != value for field, value in revisions.items()):
        raise ValueError("stale local revision")
    return admission


def _evidence_store(store):
    database = store.database_path
    if not isinstance(database, str) or not database or database == ":memory:":
        raise ValueError("persistent evidence required")
    return MappingEvidenceStore(
        Path(database).expanduser().resolve().parent / "thesis-ledger-mapping-evidence",
    )


def _identity_guard(store, request):
    admission = _local_admission(store)
    content = _evidence_store(store).read(admission.get("evidenceRef"))
    identity = resolve_hithink_fund_identity(
        content, admission, symbol=request["symbol"], start=request["start"], end=request["end"],
        data_as_of=request["dataAsOf"], observed_at=datetime.now(timezone.utc),
    )
    return admission, identity


def current_hithink_event_admission(store, key, target, manifest):
    """目录只在当前 HMAC、原文及全部准入标的范围均成立时报告 ready。"""
    if dict(key) != HITHINK_DIVIDEND_KEY or dict(target) != HITHINK_DIVIDEND_TARGET:
        return None
    try:
        admission = _local_admission(store)
        now = datetime.now(timezone.utc)
        if not _instant(admission["validFrom"]) <= _instant(now.isoformat()) < _instant(admission["validUntil"]):
            return None
        content = _evidence_store(store).read(admission.get("evidenceRef"))
        for symbol in admission["scopeSymbols"]:
            resolve_hithink_fund_identity(
                content, admission, symbol=symbol, start=admission["scopeDateFrom"],
                end=admission["scopeDateTo"], data_as_of=now.isoformat(), observed_at=now,
            )
        snapshot = store.provider_credential_snapshot("hithink")
        revision = _provider_credential_revision_from_snapshot(snapshot)
        if (snapshot.source == "environment" and revision
                and _market_v3_admission_matches_current(
                    admission, key, target, manifest, credential_revision=revision,
                )):
            return admission
    except Exception:
        return None
    return None


def _request_guard(store, request):
    try:
        return _identity_guard(store, request)
    except Exception:
        raise HiThinkEventError("not_admitted") from None


def _response(request, state, mapped, fetched):
    result, identity = mapped.result, mapped.identity
    revision, facts = result["providerRevision"], result["facts"]
    if (result.get("coverage") != {"start": request["start"], "end": request["end"], "complete": False}
            or not isinstance(revision, str) or not revision):
        raise HiThinkEventError("invalid_response")
    seen = set()
    for fact in facts:
        try:
            event = (fact["symbol"], fact["type"], fact["effectiveDate"])
            if (event in seen or fact["symbol"] != request["symbol"] or fact["market"] != "CN"
                    or fact["instrumentType"] != "ETF" or fact["type"] != "CASH_DIVIDEND"
                    or fact["provider"] != "hithink" or fact["providerRevision"] != revision
                    or fact["currency"] != identity.currency
                    or not request["start"] <= fact["effectiveDate"] <= request["end"]
                    or _instant(fact["availableAt"]) > min(_instant(request["dataAsOf"]), _instant(fetched))):
                raise ValueError()
            seen.add(event)
        except (KeyError, TypeError, ValueError):
            raise HiThinkEventError("invalid_response") from None
    admission = {name: value for name, value in state["admission"].items() if name != "recordedBy"}
    return {**request, "fetchedAt": fetched, "providerRevision": revision, "facts": facts,
            "admission": admission,
            "hithinkIdentityEvidence": {"ref": identity.evidence_ref, "sha256": identity.evidence_sha256,
                                        "content": identity.content.decode("utf-8")},
            "coverage": {"complete": False, "reason": "historical_coverage_unverified"}}


def execute_hithink_events_v3(runtime, request, *, admitted_state, check_security=None, fetch=None):
    guard = _request_guard(runtime.store, request)
    if check_security:
        check_security()
    before = admitted_state(runtime, request)
    if before["admission"] != guard[0] or _request_guard(runtime.store, request) != guard:
        raise HiThinkEventError("policy_not_applied")
    if check_security:
        check_security()
    options = {"fetch": fetch} if fetch is not None else {}
    try:
        mapped = read_hithink_mapped_fund_dividends(
            request["symbol"], database_path=runtime.store.database_path,
            start=request["start"], end=request["end"], data_as_of=request["dataAsOf"],
            read_admission=lambda: runtime._current_market_v3_admission(
                HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET,
            ),
            read_credentials=lambda: runtime.store.provider_credential_snapshot("hithink"),
            read_master_key=_secret_key, timeout_seconds=HITHINK_EVENT_TIMEOUT_SECONDS,
            **options,
        )
    except Exception:
        raise HiThinkEventError("upstream_failure") from None
    if _request_guard(runtime.store, request) != guard or mapped.identity != guard[1]:
        raise HiThinkEventError("policy_not_applied")
    after = admitted_state(runtime, request)
    if after != before:
        raise HiThinkEventError("policy_not_applied")
    fetched = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    response = _response(request, after, mapped, fetched)
    if _request_guard(runtime.store, request) != guard:
        raise HiThinkEventError("policy_not_applied")
    if check_security:
        check_security()
    return response
