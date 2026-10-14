"""HiThink ETF 分红 V3：证据先行，固定来源，读前读后复核。"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.services.hithink_fund_identity_evidence import _instant, resolve_hithink_fund_identity
from src.services.thesis_ledger_control import _provider_credential_revision_from_snapshot
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET, hithink_dividend_route_revisions,
)
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current


class HiThinkEventError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def local_admission_record(read_record):
    admission = deepcopy(read_record())
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


def _local_admission(store):
    return local_admission_record(lambda: store.get_route_admission_v3(key=HITHINK_DIVIDEND_KEY, target=HITHINK_DIVIDEND_TARGET))


def evidence_store(database):
    if not isinstance(database, str) or not database or database == ":memory:":
        raise ValueError("persistent evidence required")
    return MappingEvidenceStore(
        Path(database).expanduser().resolve().parent / "thesis-ledger-mapping-evidence",
    )


def bound_identity_guard(database, read_record, request):
    admission = local_admission_record(read_record)
    content = evidence_store(database).read(admission.get("evidenceRef"))
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
        content = evidence_store(store.database_path).read(admission.get("evidenceRef"))
        for symbol in admission["scopeSymbols"]:
            resolve_hithink_fund_identity(
                content, admission, symbol=symbol, start=admission["scopeDateFrom"],
                end=admission["scopeDateTo"], data_as_of=now.isoformat(), observed_at=now,
            )
        snapshot = store.provider_credential_snapshot("hithink")
        revision = _provider_credential_revision_from_snapshot(snapshot)
        if (snapshot.source in {"environment", "control"} and revision
                and _market_v3_admission_matches_current(
                    admission, key, target, manifest, credential_revision=revision,
                )):
            return admission
    except Exception:
        return None
    return None


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
