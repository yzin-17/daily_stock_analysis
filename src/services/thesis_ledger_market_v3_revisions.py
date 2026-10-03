"""精确来源适配修订与准入有效性，供目录及数据执行共用。"""

from __future__ import annotations

import hmac
from typing import Any, Mapping

from src.services.thesis_ledger_market_v3_adapters import (
    HITHINK_STOCK_HISTORY_SOURCE, HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1,
    HITHINK_STOCK_SOURCE_CONTRACT_REVISION, market_v3_bar_adapter_reason,
)
from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE, market_pagination_contract_v3, market_source_contract_v3,
)
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from src.services.thesis_ledger_event_v3_adapters import event_adapter_revisions, event_adapter_matches

MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION = "not-required"

# These revisions describe the local exact adapter implementation. Bump the
# affected entry whenever its source dispatch or adjustment behavior changes.
_MARKET_V3_ADAPTER_REVISIONS = {
    ("ETF", "tushare", "tushare"): {
        "none": "dsa-v3-etf-tushare-fund-daily-none-adapter-v1",
    },
    ("ETF", "akshare", "eastmoney"): {
        "none": "dsa-v3-etf-akshare-eastmoney-none-adapter-v1",
        "qfq": "dsa-v3-etf-akshare-eastmoney-qfq-adapter-v1",
        "hfq": "dsa-v3-etf-akshare-eastmoney-hfq-adapter-v1",
    },
    ("STOCK", "akshare", "eastmoney"): {
        "none": "dsa-v3-stock-akshare-eastmoney-none-adapter-v1",
        "qfq": "dsa-v3-stock-akshare-eastmoney-qfq-adapter-v1",
        "hfq": "dsa-v3-stock-akshare-eastmoney-hfq-adapter-v1",
    },
    ("STOCK", "akshare", "sina"): {
        "none": "dsa-v3-stock-akshare-sina-none-adapter-v1",
        "qfq": "dsa-v3-stock-akshare-sina-qfq-adapter-v1",
        "hfq": "dsa-v3-stock-akshare-sina-hfq-adapter-v1",
    },
    ("STOCK", "akshare", "tencent"): {
        "none": "dsa-v3-stock-akshare-tencent-none-adapter-v1",
        "qfq": "dsa-v3-stock-akshare-tencent-qfq-adapter-v1",
        "hfq": "dsa-v3-stock-akshare-tencent-hfq-adapter-v1",
    },
    ("ETF", "tencent", "tencent"): {
        "none": "dsa-v3-etf-tencent-none-adapter-v2",
        "qfq": "dsa-v3-etf-tencent-qfq-adapter-v2",
        "hfq": "dsa-v3-etf-tencent-hfq-newfqkline-adapter-v1",
    },
    ("ETF", "hithink", HITHINK_ETF_HISTORY_SOURCE): {
        "qfq": "dsa-v3-etf-hithink-fund-market-historical-qfq-adapter-v3",
    },
    ("STOCK", "hithink", HITHINK_STOCK_HISTORY_SOURCE): {
        "none": "dsa-v3-stock-hithink-financial-api-none-adapter-v1",
        "qfq": "dsa-v3-stock-hithink-financial-api-qfq-adapter-v1",
        "hfq": "dsa-v3-stock-hithink-financial-api-hfq-adapter-v1",
    },
    ("STOCK", "tencent", "tencent"): {
        "none": "dsa-v3-stock-tencent-none-adapter-v2",
        "qfq": "dsa-v3-stock-tencent-qfq-adapter-v2",
    },
}


def market_v3_current_route_revisions(
    key: Mapping[str, Any],
    target: Mapping[str, Any],
    provider_manifest: Mapping[str, Any],
    *,
    credential_version: int | None = None,
    credential_revision: str | None = None,
) -> dict[str, str] | None:
    """Resolve current local revisions only for an exact executable adapter."""
    if not isinstance(key, Mapping) or not isinstance(target, Mapping):
        return None
    if not isinstance(provider_manifest, Mapping):
        return None
    if current_data_adapter_revisions(key, target) is not None:
        if provider_manifest.get("providerId") != target.get("providerId"):
            return None
        if provider_manifest.get("requiresCredential") is not False:
            return None
        return current_data_adapter_revisions(key, target)
    if event_adapter_matches(key, target):
        provider_id = target["providerId"]
        if (provider_manifest.get("providerId") != provider_id
                or provider_manifest.get("requiresCredential") is not (provider_id in {"hithink", "rqdata", "tushare"})):
            return None
        return event_adapter_revisions(key, target, credential_revision)
    if market_v3_bar_adapter_reason(dict(key), dict(target)) is not None:
        return None

    asset_type = str(key.get("assetType") or "").strip().upper()
    provider_id = str(target.get("providerId") or "").strip().lower()
    upstream_source = str(target.get("upstreamSource") or "").strip().lower()
    adjustment = str(key.get("adjustment") or "").strip().lower()
    if provider_manifest.get("providerId") != provider_id:
        return None

    adapter_revision = _MARKET_V3_ADAPTER_REVISIONS.get(
        (asset_type, provider_id, upstream_source), {}
    ).get(adjustment)
    pagination_contract = market_pagination_contract_v3(
        asset_type,
        provider_id,
        upstream_source,
    )
    source_contract = market_source_contract_v3(
        asset_type,
        provider_id,
        upstream_source,
    )
    if (
        asset_type == "STOCK"
        and provider_id == "hithink"
        and upstream_source == HITHINK_STOCK_HISTORY_SOURCE
    ):
        pagination_contract = {
            "protocol": HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1,
            "maximumRows": None,
        }
        source_revision = HITHINK_STOCK_SOURCE_CONTRACT_REVISION
    elif provider_id == "hithink":
        source_revision = (
            source_contract.get("sourceContractRevision")
            if isinstance(source_contract, Mapping)
            else None
        )
    else:
        source_revision = (
            pagination_contract.get("protocol")
            if isinstance(pagination_contract, Mapping)
            else None
        )
    if (
        not adapter_revision
        or not isinstance(pagination_contract, Mapping)
        or not isinstance(pagination_contract.get("protocol"), str)
        or not pagination_contract["protocol"].strip()
        or not isinstance(source_revision, str)
        or not source_revision.strip()
    ):
        return None

    requires_credential = provider_manifest.get("requiresCredential")
    if requires_credential is False:
        credential_revision = MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION
    elif requires_credential is True:
        if provider_id in {"hithink", "tushare"}:
            if (
                not isinstance(credential_revision, str)
                or not credential_revision.startswith("hmac-sha256-v1:")
                or len(credential_revision.removeprefix("hmac-sha256-v1:")) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in credential_revision.removeprefix("hmac-sha256-v1:")
                )
            ):
                return None
        else:
            if (
                not isinstance(credential_version, int)
                or isinstance(credential_version, bool)
                or credential_version < 0
            ):
                return None
            credential_revision = str(credential_version)
    else:
        return None

    return {
        "adapterRevision": adapter_revision,
        "sourceRevision": source_revision.strip(),
        "credentialRevision": credential_revision,
    }


def _market_v3_admission_matches_current(
    admission: Mapping[str, Any],
    key: Mapping[str, Any],
    target: Mapping[str, Any],
    provider_manifest: Mapping[str, Any],
    *,
    credential_version: int | None = None,
    credential_revision: str | None = None,
) -> bool:
    if admission.get("admissionState") != "admitted":
        return False
    if admission.get("consumer") != "thesis-ledger":
        return False

    current_revisions = market_v3_current_route_revisions(
        key,
        target,
        provider_manifest,
        credential_version=credential_version,
        credential_revision=credential_revision,
    )
    if current_revisions is None:
        return False
    for revision_field, value in current_revisions.items():
        observed = admission.get(revision_field)
        if revision_field == "credentialRevision" and value.startswith("hmac-sha256-v1:"):
            if not isinstance(observed, str) or not hmac.compare_digest(observed, value):
                return False
        elif observed != value:
            return False

    symbols = admission.get("scopeSymbols")
    date_from = admission.get("scopeDateFrom")
    date_to = admission.get("scopeDateTo")
    if (
        not isinstance(symbols, (list, tuple))
        or not symbols
        or not isinstance(date_from, str)
        or not isinstance(date_to, str)
    ):
        return False
    return all(
        isinstance(symbol, str)
        and route_admission_scope_applies(
            dict(admission),
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
        )
        for symbol in symbols
    )
