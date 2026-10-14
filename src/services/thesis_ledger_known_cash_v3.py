"""显式方案观察协议：配置允许读取，旧完整事件准入仍保持原含义。"""

from copy import deepcopy
from datetime import date, datetime, timezone
import re

from src.services.thesis_ledger_known_cash_state_v3 import KEY, KnownCashReadError, capture_known_cash_control_v3
from src.services.thesis_ledger_hithink_known_cash import TARGET, read_hithink_known_cash
from src.services.thesis_ledger_eastmoney_known_cash import TARGET as EASTMONEY_TARGET, read_eastmoney_known_cash


POLICY = {"version": "known-announced-cash-events-v1", "entitlement": "settled-record-date-holdings",
          "receivable": "ex-date", "settlement": "assume-scheduled-payment-date",
          "missingEvents": "excluded-with-unknown-coverage", "usage": "accounting-only",
          "splitEvents": "not-modelled", "historicalAvailability": "notVerified"}
FIELDS = {"contractVersion", "protocolVersion", "requestId", "symbol", "routeKey", "routeTarget",
          "desiredRevision", "effectivePolicyRevision", "catalogRevision", "start", "end", "dataAsOf",
          "currency", "researchPolicy"}
READERS = {(TARGET["providerId"], TARGET["upstreamSource"]): read_hithink_known_cash,
           (EASTMONEY_TARGET["providerId"], EASTMONEY_TARGET["upstreamSource"]): read_eastmoney_known_cash}


def valid_research_policy(value):
    if not isinstance(value, dict):
        return False
    base = {key: item for key, item in value.items() if key != "uncertainRecords"}
    if base.get("entitlement") == "cn-etf-record-close-holdings-v2":
        base["entitlement"] = POLICY["entitlement"]
    return base == POLICY and ("uncertainRecords" not in value
                              or (isinstance(value["uncertainRecords"], str)
                                  and value["uncertainRecords"] in {"exclude", "assume-scheduled"}))


def parse_known_cash_request(payload, *, allow_legacy_control=False):
    if (not isinstance(payload, dict) or set(payload) != FIELDS
            or type(payload["contractVersion"]) is not int or payload["contractVersion"] != 3
            or payload["protocolVersion"] != "known-cash-observations-v1"
            or payload["routeKey"] != KEY or payload["currency"] != "CNY" or not valid_research_policy(payload["researchPolicy"])):
        raise KnownCashReadError("invalid_request", "invalid_research_request")
    if not allow_legacy_control and "uncertainRecords" not in payload["researchPolicy"]:
        raise KnownCashReadError("invalid_request", "invalid_research_request")
    target = payload["routeTarget"]
    if (not isinstance(target, dict) or set(target) != {"providerId", "upstreamSource", "routeIndex"}
            or type(target["routeIndex"]) is not int or target["routeIndex"] not in (0, 1)
            or any(not isinstance(target[name], str) or not target[name].strip()
                   or target[name] != target[name].strip() for name in ("providerId", "upstreamSource"))
            or not isinstance(payload["requestId"], str) or not payload["requestId"].strip()
            or payload["requestId"] != payload["requestId"].strip()
            or not isinstance(payload["symbol"], str) or re.fullmatch(r"[0-9]{6}\.(SH|SZ)", payload["symbol"]) is None
            or any(type(payload[field]) is not int or payload[field] <= 0 for field in
                   ("desiredRevision", "effectivePolicyRevision", "catalogRevision"))):
        raise KnownCashReadError("invalid_request", "invalid_research_request")
    try:
        for field in ("start", "end"):
            if date.fromisoformat(payload[field]).isoformat() != payload[field]: raise ValueError()
        if (not isinstance(payload["dataAsOf"], str)
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,1024})?(Z|[+-][0-9]{2}:[0-9]{2})", payload["dataAsOf"]) is None
                or payload["dataAsOf"].endswith("-00:00")):
            raise ValueError()
        cutoff = datetime.fromisoformat(payload["dataAsOf"].replace("Z", "+00:00"))
        if cutoff.tzinfo is None or payload["start"] > payload["end"]: raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise KnownCashReadError("invalid_request", "invalid_research_request") from None
    if payload["desiredRevision"] != payload["effectivePolicyRevision"]:
        raise KnownCashReadError("policy_not_applied", "policy_version_mismatch")
    return deepcopy(payload)



def execute_known_cash_request(runtime, payload, *, reader=None, check_security=None):
    request = parse_known_cash_request(payload)
    target = request["routeTarget"]
    adapter = READERS.get((target["providerId"], target["upstreamSource"]))
    if adapter is None:
        raise KnownCashReadError("not_adapted", "research_protocol_not_supported")
    if check_security: check_security()
    try:
        before = capture_known_cash_control_v3(runtime, request)
        result = (reader or adapter)(request, before["credential"])
        after = capture_known_cash_control_v3(runtime, request)
    except KnownCashReadError:
        raise
    except Exception:
        raise KnownCashReadError("invalid_response", "invalid_control_or_source_response") from None
    if before != after:
        raise KnownCashReadError("policy_not_applied", "configuration_changed")
    if check_security: check_security()
    try:
        source, coverage = result["source"], result["coverage"]
        identity = source["queryIdentity"]
        expected_fields = {"source", "coverage", "records", "ignoredEmptyRows"}
        if "uncertainRecords" in request["researchPolicy"]:
            expected_fields.add("assessment")
        if (set(result) != expected_fields
                or set(coverage) != {"complete", "reason"} or coverage["complete"] is not False
                or not isinstance(coverage["reason"], str) or not coverage["reason"].strip()
                or source["target"] != target or identity != {
                    "kind": "request-bound-query-v1", "requestedSymbol": request["symbol"],
                    "queryIdentifier": identity["queryIdentifier"], "currencyBasis": "explicit-research-scope",
                    "historicalIdentity": "notVerified"}
                or not isinstance(identity["queryIdentifier"], str) or not identity["queryIdentifier"].strip()
                or type(result["ignoredEmptyRows"]) is not int or result["ignoredEmptyRows"] < 0
                or not isinstance(result["records"], list) or len(result["records"]) > 5000
                or any(not isinstance(row, dict) or row.get("symbol") != request["symbol"] for row in result["records"])
                or set(source) != {"target", "providerRevision", "adapterRevision", "normalizationRevision",
                                  "responseSha256", "observedAt", "queryIdentity"}
                or any(not isinstance(source[name], str) or not source[name].strip() for name in
                       ("providerRevision", "adapterRevision", "normalizationRevision", "observedAt"))
                or re.fullmatch(r"[a-f0-9]{64}", source["responseSha256"]) is None):
            raise ValueError()
        observed = datetime.fromisoformat(source["observedAt"].replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(request["dataAsOf"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed > min(cutoff, datetime.now(timezone.utc)): raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise KnownCashReadError("invalid_response", "invalid_source_response") from None
    result = deepcopy(result)
    result["source"]["controlIdentity"] = {"providerConfigVersion": before["credential"].config_version,
                                            "credentialRevision": before["credentialRevision"]}
    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {**request, **result, "fetchedAt": fetched_at}
