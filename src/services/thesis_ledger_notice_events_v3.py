"""官方公告事件的当前准入与原字节响应绑定。"""

from datetime import datetime, timezone

from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_notice_dividends import OfficialNoticeDividendProvider, NoticeDividendError


def execute_notice_events_v3(runtime, request, *, admitted_state, error_type, check_security=None, reader=None):
    before = admitted_state(runtime, request)
    read = reader or OfficialNoticeDividendProvider().read
    try:
        observation = read(request["symbol"], request["start"], request["end"], request["dataAsOf"])
    except NoticeDividendError as error:
        code = "invalid_response" if error.code == "invalid_response" else "upstream_failure"
        raise error_type(code) from None
    except Exception:
        raise error_type("upstream_failure") from None
    after = admitted_state(runtime, request)
    if after != before:
        raise error_type("policy_not_applied")
    if check_security is not None:
        check_security()
    fetched = datetime.now(timezone.utc)
    try:
        admission = event_admission_snapshot(after["admission"], observed_at=fetched)
        evidence = observation["noticeDividendEvidence"]
        if (admission["evidenceRef"] != evidence["ref"] or admission["evidenceSha256"] != evidence["sha256"]
                or admission["sourceRevision"] != observation["provenance"]["providerRevision"]
                or not observation["coverage"]["complete"]):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise error_type("invalid_response") from None
    return {**request, "fetchedAt": fetched.isoformat(),
            "providerRevision": observation["provenance"]["providerRevision"], "admission": admission,
            "noticeDividendEvidence": evidence, "facts": observation["facts"],
            "coverage": {"complete": True, "admissionEvidenceRef": admission["evidenceRef"]}}
