"""已知现金研究的只读控制身份复核；不请求供应商或写入准入。"""

from copy import deepcopy
from datetime import datetime, timezone
import re

from src.services.thesis_ledger_known_cash_state_v3 import KEY, KnownCashReadError, capture_known_cash_control_v3
from src.services.thesis_ledger_known_cash_v3 import FIELDS, READERS, parse_known_cash_request


def parse_known_cash_control_request(payload):
    if (not isinstance(payload, dict) or set(payload) != FIELDS | {"expectedControlIdentity"}
            or payload.get("protocolVersion") != "known-cash-control-v1"):
        raise KnownCashReadError("invalid_request", "invalid_research_request")
    identity = payload["expectedControlIdentity"]
    if (not isinstance(identity, dict) or set(identity) != {"providerConfigVersion", "credentialRevision"}
            or type(identity["providerConfigVersion"]) is not int or identity["providerConfigVersion"] < 0
            or not isinstance(identity["credentialRevision"], str)
            or re.fullmatch(r"hmac-sha256-v1:[a-f0-9]{64}|not-required", identity["credentialRevision"]) is None):
        raise KnownCashReadError("invalid_request", "invalid_research_request")
    source_request = {key: value for key, value in payload.items() if key != "expectedControlIdentity"}
    source_request["protocolVersion"] = "known-cash-observations-v1"
    parsed = parse_known_cash_request(source_request, allow_legacy_control=True)
    return {**parsed, "protocolVersion": "known-cash-control-v1", "expectedControlIdentity": deepcopy(identity)}


def execute_known_cash_control_request(runtime, payload, *, check_security=None):
    request = parse_known_cash_control_request(payload)
    target = request["routeTarget"]
    if (target["providerId"], target["upstreamSource"]) not in READERS:
        raise KnownCashReadError("not_adapted", "research_protocol_not_supported")
    if check_security:
        check_security()
    try:
        before = capture_known_cash_control_v3(runtime, request)
        after = capture_known_cash_control_v3(runtime, request)
    except KnownCashReadError:
        raise
    except Exception:
        raise KnownCashReadError("invalid_response", "invalid_control_or_source_response") from None
    identity = {"providerConfigVersion": before["credential"].config_version,
                "credentialRevision": before["credentialRevision"]}
    if before != after or identity != request["expectedControlIdentity"]:
        raise KnownCashReadError("policy_not_applied", "configuration_changed")
    if check_security:
        check_security()
    route = next(row for row in before["policy"]["routes"] if row["key"] == KEY)
    sequence = [{key: row[key] for key in ("providerId", "upstreamSource", "routeIndex")}
                for row in route["targets"]]
    return {**request, "status": "verified", "readOnly": True, "upstreamRead": False,
            "checkedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "controlIdentity": identity, "targetSequence": sequence}
