"""核对准入绑定的 RQData ETF 查询身份和独立分红币种证据。"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit

from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("rqdata_identity_invalid_time")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("rqdata_identity_invalid_time")
    return parsed.astimezone(timezone.utc)


def _date(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("rqdata_identity_invalid_date")
    return value


def _document(value, fields):
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("rqdata_identity_invalid_document")
    if (not isinstance(value["documentUrl"], str)
            or value["documentUrl"] != value["documentUrl"].strip()):
        raise ValueError("rqdata_identity_invalid_document")
    url = urlsplit(value["documentUrl"])
    if url.scheme != "https" or not url.hostname or url.username or url.password:
        raise ValueError("rqdata_identity_invalid_document")
    digest = value["documentSha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("rqdata_identity_invalid_document")


@dataclass(frozen=True)
class RqDataFundIdentity:
    query_fund_code: str
    currency: str | None
    evidence_ref: str
    evidence_sha256: str
    content: bytes


def resolve_rqdata_fund_identity(
    content, admission, *, kind, symbol, start, end, data_as_of, observed_at,
):
    """准入是外部审核事实；此处校验其绑定和范围，不将文件存储升级为审核。"""
    if kind not in {"split", "dividend"}:
        raise ValueError("rqdata_identity_invalid_capability")
    if (not isinstance(symbol, str) or re.fullmatch(r"[0-9]{6}\.(SH|SZ)", symbol) is None
            or _date(start) > _date(end)):
        raise ValueError("rqdata_identity_invalid_scope")
    cutoff = _instant(data_as_of)
    current = event_admission_snapshot(admission, observed_at=observed_at)
    key = {"kind": "data", "market": "CN", "assetType": "ETF",
           "capability": "SPLIT_EVENT" if kind == "split" else "CASH_DISTRIBUTION"}
    if (current["routeKey"] != key
            or current["target"] != {"providerId": "rqdata", "upstreamSource": "rqdata"}
            or not route_admission_scope_applies(current, symbol=symbol, date_from=start, date_to=end)
            or _instant(current["recordedAt"]) > cutoff):
        raise ValueError("rqdata_identity_not_admitted")
    if not isinstance(content, bytes) or not 0 < len(content) <= 1024 * 1024:
        raise ValueError("rqdata_identity_invalid_evidence")
    digest = sha256(content).hexdigest()
    if current["evidenceRef"] != f"sha256:{digest}" or current["evidenceSha256"] != digest:
        raise ValueError("rqdata_identity_digest_mismatch")

    def unique_object(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError("rqdata_identity_duplicate_field")
            result[name] = value
        return result

    bundle = json.loads(content, object_pairs_hook=unique_object)
    if (not isinstance(bundle, dict) or set(bundle) != {"contractVersion", "kind", "mappings"}
            or type(bundle["contractVersion"]) is not int or bundle["contractVersion"] != 1
            or bundle["kind"] != "rqdata-fund-identity"
            or not isinstance(bundle["mappings"], list) or not 1 <= len(bundle["mappings"]) <= 1000):
        raise ValueError("rqdata_identity_invalid_evidence")
    identities, selected = set(), None
    fields = {"symbol", "instrumentType", "queryFundCode", "scopeDateFrom", "scopeDateTo",
              "observedAt", "identityEvidence"}
    document_fields = {"documentUrl", "documentSha256"}
    for item in bundle["mappings"]:
        if (not isinstance(item, dict) or set(item) - (fields | {"dividendCurrencyEvidence"})
                or fields - set(item)):
            raise ValueError("rqdata_identity_invalid_mapping")
        identity = item["symbol"]
        if (not isinstance(identity, str) or re.fullmatch(r"[0-9]{6}\.(SH|SZ)", identity) is None
                or identity in identities or item["instrumentType"] != "ETF"
                or item["queryFundCode"] != identity[:6]):
            raise ValueError("rqdata_identity_invalid_mapping")
        identities.add(identity)
        first, last = _date(item["scopeDateFrom"]), _date(item["scopeDateTo"])
        observation = _instant(item["observedAt"])
        if (first > last or identity not in current["scopeSymbols"]
                or first < current["scopeDateFrom"] or last > current["scopeDateTo"]
                or observation > observed_at or observation > cutoff
                or observation > _instant(current["recordedAt"])):
            raise ValueError("rqdata_identity_invalid_scope")
        _document(item["identityEvidence"], document_fields)
        currency = item.get("dividendCurrencyEvidence")
        if "dividendCurrencyEvidence" in item and currency is None:
            raise ValueError("rqdata_identity_invalid_currency")
        if currency is not None:
            _document(currency, document_fields | {"currency"})
            if not isinstance(currency["currency"], str) or currency["currency"] not in {"CNY", "HKD", "USD"}:
                raise ValueError("rqdata_identity_invalid_currency")
        if identity == symbol and first <= start and end <= last:
            if kind == "dividend" and currency is None:
                raise ValueError("rqdata_identity_missing_currency")
            selected = RqDataFundIdentity(
                item["queryFundCode"], currency["currency"] if currency else None,
                current["evidenceRef"], digest, content,
            )
    if selected is None:
        raise ValueError("rqdata_identity_missing_mapping")
    return selected
