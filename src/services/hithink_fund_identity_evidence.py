"""核对 HiThink ETF 分红的独立身份与币种原文，不授予来源或历史覆盖。"""

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
import json
import re
from urllib.parse import unquote, urlsplit

from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_KEY, HITHINK_DIVIDEND_TARGET,
)
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies


MAX_BYTES = 1024 * 1024
_SYMBOL = re.compile(r"[0-9]{6}\.(SH|SZ)\Z")
_INSTANT = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,1024}))?(Z|[+-][0-9]{2}:[0-9]{2})\Z"
)
_MAPPING_FIELDS = {
    "symbol", "instrumentType", "queryThscode", "fundType", "scopeDateFrom",
    "scopeDateTo", "observedAt", "identityEvidence", "dividendCurrencyEvidence",
}


def _date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError("hithink_identity_invalid_date") from None
    return value


def _instant(value):
    if not isinstance(value, str) or len(value) > 1050:
        raise ValueError("hithink_identity_invalid_time")
    parts = _INSTANT.fullmatch(value)
    if parts is None:
        raise ValueError("hithink_identity_invalid_time")
    year, month, day, hour, minute, second = map(int, parts.groups()[:6])
    fraction, zone = parts.groups()[6:]
    try:
        calendar_day = date(year, month, day)
    except ValueError:
        raise ValueError("hithink_identity_invalid_time") from None
    if hour > 23 or minute > 59 or second > 59 or zone == "-00:00":
        raise ValueError("hithink_identity_invalid_time")
    offset = 0
    if zone != "Z":
        offset_hour, offset_minute = int(zone[1:3]), int(zone[4:6])
        if offset_hour > 23 or offset_minute > 59:
            raise ValueError("hithink_identity_invalid_time")
        offset = (offset_hour * 60 + offset_minute) * 60
        if zone[0] == "-":
            offset = -offset
    seconds = (calendar_day.toordinal() - date(1970, 1, 1).toordinal()) * 86400
    seconds += hour * 3600 + minute * 60 + second - offset
    return seconds, (fraction or "").ljust(1024, "0")


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("hithink_identity_duplicate_field")
        result[name] = value
    return result


def _invalid_constant(_value):
    raise ValueError("hithink_identity_invalid_evidence")


def _document(value, *, currency=False):
    expected = {"documentUrl", "documentSha256"} | ({"currency"} if currency else set())
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("hithink_identity_invalid_document")
    url = value["documentUrl"]
    if (not isinstance(url, str) or not url or any(ord(char) <= 32 or ord(char) == 127 for char in url)):
        raise ValueError("hithink_identity_invalid_document")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or "\\" in parsed.netloc):
            raise ValueError()
        parsed.port
        if re.search(r"%(?![a-fA-F0-9]{2})", parsed.hostname):
            raise ValueError()
        decoded_host = unquote(parsed.hostname, errors="strict")
        if re.search(r"[\x00-\x20\x7f%/#?@\\\[\]<>^|]", decoded_host):
            raise ValueError()
        decoded_host.encode("idna")
    except (ValueError, UnicodeError):
        raise ValueError("hithink_identity_invalid_document") from None
    if not isinstance(value["documentSha256"], str) or re.fullmatch(
        r"[a-f0-9]{64}", value["documentSha256"],
    ) is None:
        raise ValueError("hithink_identity_invalid_document")
    if currency and (not isinstance(value["currency"], str)
                     or value["currency"] not in {"CNY", "HKD", "USD"}):
        raise ValueError("hithink_identity_invalid_currency")


@dataclass(frozen=True)
class HiThinkFundIdentity:
    query_thscode: str
    currency: str
    evidence_ref: str
    evidence_sha256: str
    content: bytes


def resolve_hithink_fund_identity(
    content, admission, *, symbol, start, end, data_as_of, observed_at,
) -> HiThinkFundIdentity:
    """只验证已有审核原文和精确时钟，不从代码后缀推断币种。"""
    if (not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None
            or _date(start) > _date(end)):
        raise ValueError("hithink_identity_invalid_scope")
    if (not isinstance(observed_at, datetime) or observed_at.tzinfo is None
            or observed_at.utcoffset() is None or not isinstance(admission, dict)):
        raise ValueError("hithink_identity_not_admitted")
    now = _instant(observed_at.isoformat())
    cutoff = _instant(data_as_of)
    starts, expires, recorded = (_instant(admission.get(field)) for field in
                                 ("validFrom", "validUntil", "recordedAt"))
    scope_symbols = admission.get("scopeSymbols")
    if (not isinstance(scope_symbols, (list, tuple)) or not scope_symbols
            or any(not isinstance(item, str) for item in scope_symbols)
            or _date(admission.get("scopeDateFrom")) > _date(admission.get("scopeDateTo"))):
        raise ValueError("hithink_identity_not_admitted")
    if (admission.get("consumer") != "thesis-ledger" or admission.get("status") != "admitted"
            or admission.get("admissionState") != "admitted"
            or type(admission.get("recordVersion")) is not int or admission["recordVersion"] < 1
            or admission.get("invalidatedAt") is not None
            or admission.get("invalidationReason") is not None
            or admission.get("routeKey") != HITHINK_DIVIDEND_KEY
            or admission.get("target") != HITHINK_DIVIDEND_TARGET
            or not starts <= now < expires or recorded > now or recorded > cutoff
            or not route_admission_scope_applies(admission, symbol=symbol, date_from=start, date_to=end)):
        raise ValueError("hithink_identity_not_admitted")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("hithink_identity_invalid_evidence")
    digest = sha256(content).hexdigest()
    if admission.get("evidenceRef") != f"sha256:{digest}" or admission.get("evidenceSha256") != digest:
        raise ValueError("hithink_identity_digest_mismatch")
    try:
        bundle = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object,
                            parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("hithink_identity_invalid_evidence") from None
    if (not isinstance(bundle, dict) or set(bundle) != {"contractVersion", "kind", "mappings"}
            or type(bundle["contractVersion"]) is not int or bundle["contractVersion"] != 1
            or bundle["kind"] != "hithink-fund-identity"
            or not isinstance(bundle["mappings"], list)
            or not 1 <= len(bundle["mappings"]) <= 1000):
        raise ValueError("hithink_identity_invalid_evidence")
    symbols, selected = set(), None
    for item in bundle["mappings"]:
        if not isinstance(item, dict) or set(item) != _MAPPING_FIELDS:
            raise ValueError("hithink_identity_invalid_mapping")
        identity = item["symbol"]
        if (not isinstance(identity, str) or _SYMBOL.fullmatch(identity) is None
                or identity in symbols or item["instrumentType"] != "ETF"
                or item["queryThscode"] != identity or item["fundType"] != "exchange"):
            raise ValueError("hithink_identity_invalid_mapping")
        symbols.add(identity)
        first, last = _date(item["scopeDateFrom"]), _date(item["scopeDateTo"])
        observation = _instant(item["observedAt"])
        if (first > last or identity not in scope_symbols
                or first < admission["scopeDateFrom"]
                or last > admission["scopeDateTo"]
                or observation > recorded or observation > now or observation > cutoff):
            raise ValueError("hithink_identity_invalid_scope")
        _document(item["identityEvidence"])
        _document(item["dividendCurrencyEvidence"], currency=True)
        if identity == symbol and first <= start and end <= last:
            selected = HiThinkFundIdentity(
                identity, item["dividendCurrencyEvidence"]["currency"],
                admission["evidenceRef"], digest, content,
            )
    if selected is None:
        raise ValueError("hithink_identity_missing_mapping")
    return selected
