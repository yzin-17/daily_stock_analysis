"""事件 V3 执行：固定来源，读取前后复核策略、目录和准入。"""

from copy import deepcopy
from datetime import date, datetime, timezone
import re
from typing import Any, Callable

from data_provider.eastmoney_fund_dividend_reader import fetch_fund_dividend_observations
from src.services.thesis_ledger_event_v3_adapters import event_adapter_matches
from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_split_event_reader_v3 import read_mapped_split_events
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_rqdata_event_v3 import read_rqdata_events_v3
from src.services.thesis_ledger_tushare_event_v3 import TushareEventError, execute_tushare_events_v3
from src.services.thesis_ledger_hithink_event_v3 import HiThinkEventError, execute_hithink_events_v3


class EventV3Error(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_event_request(value: Any) -> dict[str, Any]:
    fields = {"contractVersion", "requestId", "symbol", "routeKey", "routeTarget", "desiredRevision",
              "effectivePolicyRevision", "catalogRevision", "start", "end", "dataAsOf"}
    if not isinstance(value, dict) or set(value) != fields:
        raise EventV3Error("invalid_request")
    if type(value["contractVersion"]) is not int or value["contractVersion"] != 3:
        raise EventV3Error("invalid_request")
    for name in ("requestId", "symbol"):
        if not isinstance(value[name], str) or not value[name].strip() or value[name] != value[name].strip():
            raise EventV3Error("invalid_request")
    for name in ("desiredRevision", "effectivePolicyRevision", "catalogRevision"):
        if type(value[name]) is not int or value[name] <= 0:
            raise EventV3Error("invalid_request")
    if not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", value["symbol"]):
        raise EventV3Error("invalid_request")
    if value["desiredRevision"] != value["effectivePolicyRevision"]:
        raise EventV3Error("policy_not_applied")
    key, target = value["routeKey"], value["routeTarget"]
    if not isinstance(key, dict) or not isinstance(target, dict):
        raise EventV3Error("invalid_request")
    if set(target) != {"providerId", "upstreamSource", "routeIndex"}:
        raise EventV3Error("invalid_request")
    if type(target["routeIndex"]) is not int or target["routeIndex"] not in (0, 1):
        raise EventV3Error("invalid_request")
    if not event_adapter_matches(key, target):
        raise EventV3Error("not_adapted")
    try:
        for name in ("start", "end"):
            if date.fromisoformat(value[name]).isoformat() != value[name]:
                raise ValueError()
        as_of = datetime.fromisoformat(value["dataAsOf"].replace("Z", "+00:00"))
        if as_of.tzinfo is None or value["start"] > value["end"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise EventV3Error("invalid_request") from None
    return deepcopy(value)


def _admitted_state(runtime: Any, request: dict[str, Any]) -> dict[str, Any]:
    key, target = request["routeKey"], request["routeTarget"]
    policy = runtime.store.effective_policy_v3()
    if (not isinstance(policy, dict) or policy.get("enabled") is not True
            or any(type(policy.get(field)) is not int for field in
                   ("contractVersion", "revision", "sourceDesiredRevision"))
            or policy.get("contractVersion") != 3
            or policy.get("revision") != request["effectivePolicyRevision"]
            or policy.get("sourceDesiredRevision") != request["desiredRevision"]):
        raise EventV3Error("policy_not_applied")
    routes = policy.get("routes")
    if not isinstance(routes, list):
        raise EventV3Error("invalid_response")
    matches = [item for item in routes if isinstance(item, dict) and item.get("key") == key]
    if len(matches) != 1:
        raise EventV3Error("not_admitted")
    targets = matches[0].get("targets")
    if not isinstance(targets, list) or not 1 <= len(targets) <= 2:
        raise EventV3Error("invalid_response")
    identities = set()
    for index, item in enumerate(targets):
        if (not isinstance(item, dict) or type(item.get("routeIndex")) is not int
                or item["routeIndex"] != index):
            raise EventV3Error("invalid_response")
        identity = (item.get("providerId"), item.get("upstreamSource"))
        if not all(isinstance(value, str) and value for value in identity) or identity in identities:
            raise EventV3Error("invalid_response")
        identities.add(identity)
    selected = [item for index, item in enumerate(targets) if isinstance(item, dict)
                and item.get("routeIndex") == index
                and all(item.get(name) == value for name, value in target.items())]
    if len(selected) != 1 or selected[0].get("eligible") is not True or selected[0].get("reason") is not None:
        raise EventV3Error("not_admitted")
    catalog = runtime.market_route_catalog_v3()
    if (not isinstance(catalog, dict) or catalog.get("integrity") != "complete"
            or type(catalog.get("catalogRevision")) is not int
            or catalog.get("catalogRevision") != request["catalogRevision"]):
        raise EventV3Error("policy_not_applied")
    identity = {name: target[name] for name in ("providerId", "upstreamSource")}
    entries = [entry for entry in catalog.get("entries", [])
               if entry.get("key") == key and entry.get("target") == identity]
    if len(entries) != 1 or entries[0].get("state") != "ready":
        raise EventV3Error("not_admitted")
    admission = runtime._current_market_v3_admission(key, identity)
    if not isinstance(admission, dict) or not route_admission_scope_applies(
        admission, symbol=request["symbol"], date_from=request["start"], date_to=request["end"],
    ):
        raise EventV3Error("not_admitted")
    return deepcopy({"policy": {k: v for k, v in policy.items() if k != "appliedAt"}, "catalog": {k: catalog[k] for k in
                     ("catalogRevision", "integrity", "entries")}, "admission": admission})


def execute_event_request(
    runtime: Any, payload: Any, *, reader: Callable[..., dict[str, Any]] | None = None, check_security=None,
) -> dict[str, Any]:
    request = parse_event_request(payload)
    if request["routeTarget"]["providerId"] == "tushare":
        try:
            return execute_tushare_events_v3(runtime, request, admitted_state=_admitted_state, check_security=check_security)
        except TushareEventError as error:
            raise EventV3Error(error.code) from None
    if request["routeTarget"]["providerId"] == "hithink":
        try:
            return execute_hithink_events_v3(
                runtime, request, admitted_state=_admitted_state,
                check_security=check_security, fetch=reader,
            )
        except HiThinkEventError as error:
            raise EventV3Error(error.code) from None
    before = _admitted_state(runtime, request)
    try:
        if request["routeTarget"]["providerId"] == "rqdata":
            result = read_rqdata_events_v3(runtime, request)
        elif request["routeKey"]["capability"] == "SPLIT_EVENT":
            result = read_mapped_split_events(runtime.store.database_path, request, before["admission"], reader=reader)
        else:
            read = reader or fetch_fund_dividend_observations
            result = read(request["symbol"], start=request["start"], end=request["end"])
    except Exception:
        raise EventV3Error("upstream_failure") from None
    after = _admitted_state(runtime, request)
    if after != before:
        raise EventV3Error("policy_not_applied")
    fetched = datetime.now(timezone.utc)
    try:
        admission = event_admission_snapshot(after["admission"], observed_at=fetched)
    except (ValueError, TypeError):
        raise EventV3Error("invalid_response") from None
    as_of = datetime.fromisoformat(request["dataAsOf"].replace("Z", "+00:00"))
    facts = result.get("facts")
    if not isinstance(facts, list) or any(not isinstance(fact, dict) for fact in facts):
        raise EventV3Error("invalid_response")
    revisions = {fact.get("providerRevision") for fact in facts}
    if len(revisions) > 1:
        raise EventV3Error("invalid_response")
    for fact in facts:
        try:
            available = datetime.fromisoformat(fact["availableAt"].replace("Z", "+00:00"))
            if (fact["symbol"] != request["symbol"] or fact["market"] != "CN"
                    or fact["instrumentType"] != "ETF"
                    or fact["type"] not in ({"SPLIT", "REVERSE_SPLIT"}
                                            if request["routeKey"]["capability"] == "SPLIT_EVENT" else {"CASH_DIVIDEND"})
                    or fact["provider"] != request["routeTarget"]["providerId"]
                    or not request["start"] <= fact["effectiveDate"] <= request["end"]
                    or available > as_of or available > fetched):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise EventV3Error("invalid_response") from None
    revision = next(iter(revisions), None) or result.get("providerRevision")
    if not isinstance(revision, str) or not revision.strip():
        raise EventV3Error("invalid_response")
    evidence = {}
    if request["routeTarget"]["providerId"] == "rqdata":
        evidence = {"identityEvidence": result["identityEvidence"]}
    elif request["routeKey"]["capability"] == "SPLIT_EVENT":
        evidence = {"dateMappingEvidence": result["dateMappingEvidence"]}
    return {**request, "fetchedAt": fetched.isoformat().replace("+00:00", "Z"),
            "providerRevision": revision, "facts": facts, "admission": admission,
            **evidence, "coverage": {"complete": False, "reason": "historical_coverage_unverified"}}
