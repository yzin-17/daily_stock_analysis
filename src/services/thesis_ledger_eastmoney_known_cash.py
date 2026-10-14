"""东财普通现金研究适配：无需项目窗口准入，保留来源缺字段及配置门禁。"""

from datetime import datetime, timezone
from hashlib import sha256
from data_provider.eastmoney_fund_dividend_reader import fetch_fund_cash_research_page
from data_provider.eastmoney_dividend_research_records import REVISION, normalize_eastmoney_research_records
from src.services.thesis_ledger_known_cash_state_v3 import KnownCashReadError

TARGET = {"providerId": "akshare", "upstreamSource": "eastmoney"}


def read_eastmoney_known_cash(request, snapshot, *, fetch=fetch_fund_cash_research_page):
    if (snapshot.provider_id != "akshare" or request["currency"] != "CNY"
            or any(request["routeTarget"].get(key) != value for key, value in TARGET.items())):
        raise KnownCashReadError("invalid_request", "invalid_research_scope")
    try:
        raw = fetch(request["symbol"])
    except ValueError:
        raise KnownCashReadError("invalid_response", "invalid_source_response") from None
    except Exception:
        raise KnownCashReadError("upstream_failure", "source_unavailable") from None
    try:
        observed = datetime.fromisoformat(raw["observedAt"].replace("Z", "+00:00"))
        cutoff = datetime.fromisoformat(request["dataAsOf"].replace("Z", "+00:00"))
        if (raw["symbol"] != request["symbol"] or observed.tzinfo is None
                or observed > min(cutoff, datetime.now(timezone.utc))
                or sha256(raw["rawBusinessResponse"].encode()).hexdigest() != raw["responseSha256"]):
            raise ValueError("来源身份/原文/时钟无效")
        normalized = normalize_eastmoney_research_records(raw, request)
        return {"source": {"target": dict(request["routeTarget"]),
                "providerRevision": "eastmoney-fund-cash-page-v1:" + raw["responseSha256"],
                "adapterRevision": "dsa-eastmoney-known-cash-v1", "normalizationRevision": REVISION,
                "responseSha256": raw["responseSha256"], "observedAt": raw["observedAt"],
                "queryIdentity": {"kind": "request-bound-query-v1", "requestedSymbol": request["symbol"],
                                  "queryIdentifier": request["symbol"].split(".")[0],
                                  "currencyBasis": "explicit-research-scope", "historicalIdentity": "notVerified"}},
                "coverage": {"complete": False, "reason": "historical_coverage_unverified"},
                "ignoredEmptyRows": normalized["assessment"]["counts"]["placeholder"], **normalized}
    except (KeyError, TypeError, ValueError, AttributeError):
        raise KnownCashReadError("invalid_response", "invalid_source_response") from None
