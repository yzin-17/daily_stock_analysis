"""从港美股原始事实包投影当前日级状态能力，保持盘中区间和原始公告身份。"""

from datetime import datetime
from hashlib import sha256
import json

from data_provider.legacy_status_projection import read_intraday_status
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding
from src.services.thesis_ledger_legacy_status_contract_v1 import StatusRequest
from src.services.thesis_ledger_multimarket_calendar import multimarket_calendar_evidence
from src.services.thesis_ledger_multimarket_rules import SOURCES
from src.services.thesis_ledger_status_capabilities_v1 import TRADING_CONTRACT, TradingResult, TradingRequest
from src.services.thesis_ledger_status_query_support_v1 import TradingSupport

ADAPTER_REVISION = "bounded-multimarket-rules-adapter-v1"


def read_multimarket_daily_status(query, context):
    old_key = {**query.routeKey.model_dump(), "capability": "HISTORICAL_TRADING_STATUS"}
    request = StatusRequest.model_validate(
        query.model_dump(exclude={"capabilityVersion", "policyRevision"}) | {"routeKey": old_key}
    )
    facts = read_intraday_status(request, context)
    observed = facts.provenance.fetchedAt
    calendar = multimarket_calendar_evidence(
        query.routeKey.market, query.range.start, query.range.end, datetime.fromisoformat(observed)
    )
    evidence = [
        {"id": doc.id, "kind": "document", "sha256": doc.sha256,
         "hashScope": "originalBytes" if doc.sha256 is not None else "unknown",
         "reference": doc.url, "observedAt": doc.observedAt, "availableAt": None}
        for doc in facts.sourceDocuments
    ]
    expected = calendar["expectedSessionDates"] if calendar else []
    calendar_refs = []
    if calendar:
        digest = sha256(json.dumps(calendar, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        calendar_refs = ["calendar:" + digest]
        evidence.append({"id": calendar_refs[0], "kind": "calendar", "sha256": digest,
                         "hashScope": "normalizedResponse", "observedAt": observed,
                         "reference": None, "availableAt": None})
    return TradingResult.model_validate({
        **query.model_dump(),
        "provenance": {"adapterRevision": ADAPTER_REVISION,
                       "sourceRevision": facts.provenance.providerRevision},
        "clock": {"observedAt": observed, "upstreamAt": None,
                  "timezone": facts.provenance.timezone, "basis": "sessionDate"},
        "evidence": evidence,
        "days": [{"date": day.date, "state": day.state,
                  "intradayHalts": [halt.model_dump() for halt in day.intradayHalts or []],
                  "evidenceRefs": day.sourceDocumentIds or []} for day in facts.days],
        "coverage": {"range": query.range.model_dump(), "expectedDates": expected,
                     "calendarUnknownDates": [] if calendar else [day.date for day in facts.days],
                     "unknownDates": expected, "calendarEvidenceRefs": calendar_refs,
                     "complete": bool(calendar and not expected)},
    })


def daily_status_bindings(provider):
    return tuple(
        QueryBinding(TRADING_CONTRACT, ExactKey(market, "STOCK", "DAILY_TRADING_STATUS"), source,
                     ADAPTER_REVISION, filename + ":v1", read_multimarket_daily_status, gated=False,
                     query_support=TradingSupport(maximumWindowDays=3661))
        for (market, capability, provider_id, source), filename in SOURCES.items()
        if provider_id == provider and capability == "HISTORICAL_TRADING_STATUS"
    )


def probe_multimarket_status(request, policy_revision, adapter):
    if policy_revision is None:
        raise CapabilityContractError("control_rejected", "状态检查缺少有效政策修订")
    query = TradingRequest.model_validate(
        {**request, "capabilityVersion": 1, "policyRevision": policy_revision}
    )
    read_multimarket_daily_status(query, ReadContext(adapter, probe=True))
