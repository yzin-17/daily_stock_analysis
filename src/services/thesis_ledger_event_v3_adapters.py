"""事件 V3 的精确适配库存；库存不授予来源准入或事件覆盖。"""

from typing import Any, Mapping
import re

from src.services.thesis_ledger_tushare_mapped_read import (
    TUSHARE_FUND_DIV_ADAPTER_REVISION, TUSHARE_FUND_DIV_SOURCE_REVISION,
)
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET,
    hithink_dividend_route_matches, hithink_dividend_route_revisions,
)


EVENT_KEY = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"}
EVENT_TARGET = {"providerId": "akshare", "upstreamSource": "eastmoney"}
EVENT_REVISIONS = {
    "adapterRevision": "dsa-eastmoney-fund-dividend-v1",
    "sourceRevision": "eastmoney-fund-fh-pageinfo-json-v1",
    "credentialRevision": "not-required",
}
SPLIT_KEY = {**EVENT_KEY, "capability": "SPLIT_EVENT"}
SPLIT_REVISIONS = {
    "adapterRevision": "dsa-eastmoney-fund-split-mapped-v1",
    "sourceRevision": "eastmoney-fund-cf-pageinfo-json-v1",
    "credentialRevision": "not-required",
}
RQDATA_TARGET = {"providerId": "rqdata", "upstreamSource": "rqdata"}
TUSHARE_TARGET = {"providerId": "tushare", "upstreamSource": "tushare"}
RQDATA_REVISIONS = {
    "CASH_DISTRIBUTION": {"adapterRevision": "dsa-rqdata-etf-dividend-identity-v1",
                          "sourceRevision": "rqdatac-3.7.1-fund-get-dividend-v1"},
    "SPLIT_EVENT": {"adapterRevision": "dsa-rqdata-etf-split-identity-v1",
                    "sourceRevision": "rqdatac-3.7.1-fund-get-split-v1"},
}


def event_adapter_matches(key: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    if hithink_dividend_route_matches(key, target):
        return True
    if dict(key) == EVENT_KEY and all(target.get(name) == value for name, value in TUSHARE_TARGET.items()):
        return True
    return dict(key) in (EVENT_KEY, SPLIT_KEY) and any(
        all(target.get(name) == value for name, value in identity.items())
        for identity in (EVENT_TARGET, RQDATA_TARGET))


def event_adapter_revisions(key: Mapping[str, Any], target=None, credential_revision=None) -> dict[str, str] | None:
    if target is not None and not event_adapter_matches(key, target):
        return None
    if target is not None and hithink_dividend_route_matches(key, target):
        return hithink_dividend_route_revisions(key, target, credential_revision)
    if target is not None and target.get("providerId") in {"rqdata", "tushare"}:
        if not isinstance(credential_revision, str) or not re.fullmatch(r'hmac-sha256-v1:[a-f0-9]{64}', credential_revision):
            return None
        if target.get("providerId") == "tushare":
            return {"adapterRevision": TUSHARE_FUND_DIV_ADAPTER_REVISION,
                    "sourceRevision": TUSHARE_FUND_DIV_SOURCE_REVISION,
                    "credentialRevision": credential_revision}
        return {**RQDATA_REVISIONS[key["capability"]], "credentialRevision": credential_revision}
    return dict(SPLIT_REVISIONS if dict(key) == SPLIT_KEY else EVENT_REVISIONS)


def iter_event_adapters():
    yield dict(EVENT_KEY), dict(EVENT_TARGET), True
    yield dict(SPLIT_KEY), dict(EVENT_TARGET), True
    yield dict(EVENT_KEY), dict(RQDATA_TARGET), True
    yield dict(SPLIT_KEY), dict(RQDATA_TARGET), True
    yield dict(EVENT_KEY), dict(TUSHARE_TARGET), True
    yield dict(HITHINK_DIVIDEND_KEY), dict(HITHINK_DIVIDEND_TARGET), True
