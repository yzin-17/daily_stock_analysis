"""仅为已与深交所逐日及月报对账的 ETF 窗口声明原生量额单位。"""

from __future__ import annotations

from datetime import date
from typing import Any

from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
    HITHINK_ETF_NATIVE_FIELD_CONTRACT_V1,
    HITHINK_ETF_NATIVE_FIELD_CONTRACT_V2,
    HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
)


_EVIDENCE_ID = "szse-159516-hithink-etf-units-20260430-20260809-v1"
_REVIEWED_SOURCE_REVISION = "dsa-hithink-etf-request-contract-v1"
_SCOPE_START = date(2026, 4, 30)
_SCOPE_END = date(2026, 8, 9)


def _reviewed_window(symbol: str, start: str, end: str, source_revision: str) -> bool:
    if symbol != "159516.SZ" or source_revision != _REVIEWED_SOURCE_REVISION:
        return False
    try:
        first = date.fromisoformat(start)
        last = date.fromisoformat(end)
    except (TypeError, ValueError):
        return False
    return (
        first.isoformat() == start
        and last.isoformat() == end
        and _SCOPE_START <= first <= last <= _SCOPE_END
    )


def hithink_etf_field_contract(
    *, symbol: str, start: str, end: str, source_revision: str,
) -> dict[str, Any]:
    """Build a request-bound contract; every unreviewed range remains unknown."""
    contract: dict[str, Any] = {
        "contractVersion": HITHINK_ETF_NATIVE_FIELD_CONTRACT_V1,
        "providerId": "hithink",
        "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
        "assetType": "ETF",
        "adjustment": "qfq",
        "volumeUnit": "unknown",
        "amountUnit": "unknown",
        "valuesConverted": False,
    }
    if _reviewed_window(symbol, start, end, source_revision):
        contract.update({
            "contractVersion": HITHINK_ETF_NATIVE_FIELD_CONTRACT_V2,
            "volumeUnit": "fund-unit",
            "amountUnit": "CNY",
            "unitEvidenceId": _EVIDENCE_ID,
            "sourceContractRevision": source_revision,
            "requestSymbol": symbol,
            "requestedStart": start,
            "requestedEnd": end,
        })
    return contract


def hithink_etf_field_contract_is_valid(
    contract: Any, request: Any,
) -> bool:
    """Reject a known-unit assertion unless it matches the reviewed request."""
    if not isinstance(contract, dict):
        return False
    if contract.get("volumeUnit") == "unknown" and contract.get("amountUnit") == "unknown":
        return contract == hithink_etf_field_contract(
            symbol="", start="", end="", source_revision="",
        )
    if not isinstance(request, dict):
        return False
    expected = hithink_etf_field_contract(
        symbol=request.get("symbol"),
        start=request.get("start"),
        end=request.get("end"),
        source_revision=HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    )
    return expected["volumeUnit"] != "unknown" and contract == expected
