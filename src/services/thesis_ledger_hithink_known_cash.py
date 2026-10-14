"""HiThink方案观察：固定查询/凭证，研究范围不是供应商身份或币种回显。"""

from datetime import datetime, timezone
import math
import re
import hashlib

from data_provider.hithink_fund_dividend_reader import (
    HiThinkFundDividendReadError, fetch_hithink_fund_dividends,
)
from data_provider.hithink_fund_dividend_semantics import (
    PLAN_ANNOUNCED_LABEL, PLAN_ANNOUNCED_SEMANTICS, RESEARCH_RECORDS_SEMANTICS,
)
from data_provider.hithink_dividend_research_records import normalize_research_records
from data_provider.hithink_fund_dividends import normalize_hithink_fund_dividends
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_known_cash_state_v3 import KnownCashReadError


ADAPTER_REVISION = "dsa-hithink-known-cash-observations-v1"
NORMALIZATION_REVISION = "hithink-announced-cash-records-v1"
TARGET = {"providerId": "hithink", "upstreamSource": "fund-corporate-actions-dividends"}



def read_hithink_known_cash(request, snapshot, *, fetch=fetch_hithink_fund_dividends, timeout_seconds=10):
    """一次完整传输，按请求除息区间筛选；不授予完整覆盖或已支付事实。"""
    if (not isinstance(snapshot, ProviderCredentialSnapshot) or snapshot.provider_id != "hithink"
            or snapshot.source not in {"control", "environment"} or snapshot.method != "api_key"
            or set(snapshot.values) != {"apiKey"} or not isinstance(snapshot.values["apiKey"], str)
            or not snapshot.values["apiKey"].strip()):
        raise KnownCashReadError("not_admitted", "credential_missing")
    if (not re.fullmatch(r"[0-9]{6}\.(SH|SZ)", request["symbol"])
            or request["currency"] != "CNY"
            or any(request["routeTarget"].get(key) != value for key, value in TARGET.items())
            or type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 10):
        raise KnownCashReadError("invalid_request", "invalid_research_scope")
    try:
        semantics = (RESEARCH_RECORDS_SEMANTICS if "uncertainRecords" in request["researchPolicy"]
                     else PLAN_ANNOUNCED_SEMANTICS)
        raw = fetch(request["symbol"], fund_type="exchange", api_key=snapshot.values["apiKey"],
                    timeout_seconds=timeout_seconds, research_semantics=semantics)
    except HiThinkFundDividendReadError as error:
        code = "invalid_response" if error.code in {"invalid_response", "count_mismatch", "invalid_clock"} else "upstream_failure"
        raise KnownCashReadError(code, error.code) from None
    except Exception:
        raise KnownCashReadError("upstream_failure", "source_unavailable") from None
    try:
        if (not isinstance(raw, dict) or raw.get("symbol") != request["symbol"]
                or raw.get("fundType") != "exchange" or raw.get("historyComplete") is not False
                or raw.get("researchSemantics") != semantics
                or not isinstance(raw.get("items"), list) or len(raw["items"]) > 5000
                or not isinstance(raw.get("responseSha256"), str)
                or re.fullmatch(r"[a-f0-9]{64}", raw["responseSha256"]) is None
                or type(raw.get("ignoredEmptyRows")) is not int or raw["ignoredEmptyRows"] < 0):
            raise ValueError()
        observed = datetime.fromisoformat(raw["observedAt"].replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(request["dataAsOf"].replace("Z", "+00:00"))
        if (observed.tzinfo is None or cutoff.tzinfo is None
                or observed > cutoff or observed > datetime.now(timezone.utc)):
            raise ValueError()
        provider_revision = f"hithink-fund-dividends-content-v1:{raw['responseSha256']}"
        if semantics == RESEARCH_RECORDS_SEMANTICS:
            from zoneinfo import ZoneInfo
            result = normalize_research_records(raw, {**request, "observedDate": observed.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()})
            return _research_response(request, raw, provider_revision, result)
        result = normalize_hithink_fund_dividends(
            raw["items"], request["symbol"], instrument_type="ETF", currency=request["currency"],
            start=request["start"], end=request["end"], observed_at=observed,
            provider_revision=provider_revision, research_semantics=PLAN_ANNOUNCED_SEMANTICS,
        )
        records, preliminary = [], 0
        for row in result["observations"]:
            if row["planProgress"] == "预案":
                preliminary += 1
                continue
            if row["planProgress"] not in {"实施", PLAN_ANNOUNCED_LABEL}:
                raise ValueError()
            dates = [row[name] for name in ("announcementDate", "recordDate", "effectiveDate", "paymentDate")]
            if (not all(isinstance(value, str) for value in dates) or dates[0] > dates[1]
                    or dates[1] >= dates[2] or dates[3] < dates[2]):
                raise ValueError()
            records.append({
                "symbol": request["symbol"], "announcementDate": dates[0], "recordDate": dates[1],
                "effectiveDate": dates[2], "paymentDate": dates[3], "amountPerFundUnit": row["cashAmount"],
                "currency": row["currency"], "planStatus": "implementation-plan-announced",
                "statusBasis": "research-assumption" if row["progressSemanticsBasis"] == "research-assumption" else "source-defined",
                "sourcePlanStatus": row["rawPlanProgress"], "sourceSemanticsRevision": row["progressSemantics"],
                "sourceAmount": {"value": row["cashBeforeTaxPerTen"], "unit": row["cashOriginalUnit"]},
            })
        reason = "historical_coverage_unverified"
        if preliminary:
            reason += f";preliminary_plans_excluded={preliminary}"
        return {
            "source": {"target": dict(request["routeTarget"]), "providerRevision": provider_revision,
                       "adapterRevision": ADAPTER_REVISION, "normalizationRevision": NORMALIZATION_REVISION,
                       "responseSha256": raw["responseSha256"], "observedAt": raw["observedAt"],
                       "queryIdentity": {"kind": "request-bound-query-v1", "requestedSymbol": request["symbol"],
                                         "queryIdentifier": request["symbol"], "currencyBasis": "explicit-research-scope",
                                         "historicalIdentity": "notVerified"}},
            "coverage": {"complete": False, "reason": reason}, "records": records,
            "ignoredEmptyRows": raw["ignoredEmptyRows"] + result["ignoredEmptyRows"],
        }
    except (KeyError, TypeError, ValueError, AttributeError):
        raise KnownCashReadError("invalid_response", "invalid_source_response") from None


def _research_response(request, raw, provider_revision, result):
    if hashlib.sha256(raw["rawBusinessResponse"].encode()).hexdigest() != raw["responseSha256"]:
        raise ValueError("HiThink 原业务响应与摘要不一致")
    return {
        "source": {"target": dict(request["routeTarget"]), "providerRevision": provider_revision,
                   "adapterRevision": ADAPTER_REVISION, "normalizationRevision": RESEARCH_RECORDS_SEMANTICS,
                   "responseSha256": raw["responseSha256"], "observedAt": raw["observedAt"],
                   "queryIdentity": {"kind": "request-bound-query-v1", "requestedSymbol": request["symbol"],
                                     "queryIdentifier": request["symbol"], "currencyBasis": "explicit-research-scope",
                                     "historicalIdentity": "notVerified"}},
        "coverage": {"complete": False, "reason": "historical_coverage_unverified"},
        "ignoredEmptyRows": raw["ignoredEmptyRows"] + result["assessment"]["counts"]["placeholder"],
        **result,
    }
