"""HiThink 单标报价的精确来源准入判定。"""

from __future__ import annotations

import hmac
import re
from datetime import datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE,
    HITHINK_STOCK_SNAPSHOT_SOURCE,
)
from .thesis_ledger_route_admission_v3 import route_admission_scope_applies


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SYMBOLS = {
    "STOCK": re.compile(r"^[0-9]{6}\.(?:SH|SZ|BJ)$"),
    "ETF": re.compile(r"^[0-9]{6}\.(?:SH|SZ)$"),
}
_QUOTE_REVISIONS = {
    ("STOCK", HITHINK_STOCK_SNAPSHOT_SOURCE): (
        "dsa-hithink-stock-quote-adapter-v1",
        "dsa-hithink-stock-snapshot-request-v1",
    ),
    ("ETF", HITHINK_ETF_SNAPSHOT_SOURCE): (
        "dsa-hithink-etf-quote-adapter-v1",
        "dsa-hithink-etf-snapshot-request-v1",
    ),
}


def hithink_quote_route_key(asset_type: str) -> dict[str, str]:
    """与 RouteAdmissionV3 共用的精确数据能力身份。"""
    if asset_type not in {"STOCK", "ETF"}:
        raise ValueError("HiThink quote asset type unsupported")
    return {
        "kind": "data", "market": "CN", "assetType": asset_type,
        "capability": "REALTIME_QUOTE",
    }


def iter_hithink_quote_routes():
    """Enumerate the two source-pinned quote adapters exposed to Control."""
    for asset_type, source in sorted(_QUOTE_REVISIONS):
        yield hithink_quote_route_key(asset_type), {
            "providerId": "hithink", "upstreamSource": source,
        }


def hithink_quote_revisions(asset_type: str, source: str) -> dict[str, str] | None:
    values = _QUOTE_REVISIONS.get((asset_type, source))
    if values is None:
        return None
    return {"adapterRevision": values[0], "sourceRevision": values[1]}


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def hithink_quote_admission_matches_current(
    admission: Mapping[str, Any] | None,
    *,
    asset_type: str,
    source: str,
    symbol: str,
    credential_revision: str | None,
    now: datetime,
) -> bool:
    """核对当前准入、精确身份、交易日范围与环境凭据修订。"""
    revisions = hithink_quote_revisions(asset_type, source)
    if (
        revisions is None or not isinstance(admission, Mapping)
        or not isinstance(now, datetime) or now.tzinfo is None
        or not isinstance(symbol, str) or _SYMBOLS[asset_type].fullmatch(symbol) is None
        or admission.get("consumer") != "thesis-ledger"
        or admission.get("status") != "admitted"
        or admission.get("admissionState") != "admitted"
        or admission.get("invalidatedAt") is not None
        or admission.get("routeKey") != hithink_quote_route_key(asset_type)
        or admission.get("target") != {"providerId": "hithink", "upstreamSource": source}
    ):
        return False
    if any(admission.get(field) != value for field, value in revisions.items()):
        return False
    evidence_ref = admission.get("evidenceRef")
    evidence_hash = admission.get("evidenceSha256")
    if (
        not isinstance(evidence_ref, str) or not evidence_ref.strip()
        or not isinstance(evidence_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", evidence_hash) is None
        or not isinstance(admission.get("recordVersion"), int)
        or isinstance(admission["recordVersion"], bool)
        or admission["recordVersion"] < 1
    ):
        return False
    recorded = _utc(admission.get("recordedAt"))
    start = _utc(admission.get("validFrom"))
    end = _utc(admission.get("validUntil"))
    current = now.astimezone(timezone.utc)
    if recorded is None or start is None or end is None or not recorded <= current or not start <= current < end:
        return False
    observed = admission.get("credentialRevision")
    if (
        not isinstance(credential_revision, str)
        or re.fullmatch(r"hmac-sha256-v1:[0-9a-f]{64}", credential_revision) is None
        or not isinstance(observed, str)
        or not hmac.compare_digest(observed, credential_revision)
    ):
        return False
    day = now.astimezone(_SHANGHAI).date().isoformat()
    return route_admission_scope_applies(
        dict(admission), symbol=symbol, date_from=day, date_to=day,
    )
