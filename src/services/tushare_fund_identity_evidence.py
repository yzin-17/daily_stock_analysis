"""核对 Tushare ETF 分红的独立身份、币种原字节及精确准入时刻。"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import re
from urllib.parse import unquote, urlsplit

from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies


MAX_BYTES = 1024 * 1024
_SYMBOL = re.compile(r"[0-9]{6}\.(SH|SZ)")
_INSTANT = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(?:\.([0-9]{1,1024}))?(Z|[+-][0-9]{2}:[0-9]{2})"
)
_MAPPING_FIELDS = {
    "symbol", "instrumentType", "queryFundCode", "scopeDateFrom", "scopeDateTo",
    "observedAt", "identityEvidence", "dividendCurrencyEvidence",
}


def _instant(value):
    if not isinstance(value, str) or len(value) > 1050:
        raise ValueError("tushare_identity_invalid_time")
    parts = _INSTANT.fullmatch(value)
    if parts is None:
        raise ValueError("tushare_identity_invalid_time")
    year, month, day, hour, minute, second = map(int, parts.groups()[:6])
    fraction, zone = parts.groups()[6:]
    try:
        calendar_day = date(year, month, day)
    except ValueError:
        raise ValueError("tushare_identity_invalid_time") from None
    if hour > 23 or minute > 59 or second > 59 or zone == "-00:00":
        raise ValueError("tushare_identity_invalid_time")
    offset = 0
    if zone != "Z":
        offset_hour, offset_minute = int(zone[1:3]), int(zone[4:6])
        if offset_hour > 23 or offset_minute > 59:
            raise ValueError("tushare_identity_invalid_time")
        offset = (offset_hour * 60 + offset_minute) * 60
        if zone[0] == "-":
            offset = -offset
    seconds = (calendar_day.toordinal() - date(1970, 1, 1).toordinal()) * 86400
    seconds += hour * 3600 + minute * 60 + second - offset
    # 整数秒和等宽小数分别比较；小数从未进入 datetime 或浮点转换。
    return seconds, (fraction or "").ljust(1024, "0")


def _date(value):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except ValueError:
        raise ValueError("tushare_identity_invalid_date") from None
    return value


def _document(value, *, currency=False):
    fields = {"documentUrl", "documentSha256"}
    if currency:
        fields.add("currency")
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("tushare_identity_invalid_document")
    url = value["documentUrl"]
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise ValueError("tushare_identity_invalid_document")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or "\\" in parsed.netloc
                or parsed.username is not None or parsed.password is not None):
            raise ValueError()
        parsed.port
        host = parsed.hostname
        if re.search(r"%(?![a-fA-F0-9]{2})", host):
            raise ValueError()
        decoded_host = unquote(host, errors="strict")
        if (re.search(r"[\x00-\x20\x7f%/#?@\\\[\]<>^|]", decoded_host)
                or (":" in decoded_host and not parsed.netloc.startswith("["))):
            raise ValueError()
        decoded_host.encode("idna")
    except (ValueError, UnicodeError):
        raise ValueError("tushare_identity_invalid_document") from None
    digest = value["documentSha256"]
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        raise ValueError("tushare_identity_invalid_document")
    if currency and (not isinstance(value["currency"], str) or value["currency"] not in {"CNY", "HKD", "USD"}):
        raise ValueError("tushare_identity_invalid_currency")


def _current_admission(admission, *, observed_at, cutoff):
    if not isinstance(admission, dict) or not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("tushare_identity_not_admitted")
    now = _instant(observed_at.isoformat())
    starts, expires, recorded = (_instant(admission.get(field)) for field in ("validFrom", "validUntil", "recordedAt"))
    if not starts <= now < expires or recorded > now or recorded > cutoff:
        raise ValueError("tushare_identity_not_admitted")
    # 原时刻已经精确核验。仅供旧原语的状态检查，向上投影到期边界，绝不返回投影字段。
    projected = dict(admission)
    try:
        native_expiry = datetime.fromisoformat(admission["validUntil"].replace("Z", "+00:00"))
        if expires[1][6:].strip("0"):
            native_expiry += timedelta(microseconds=1)
        projected["validUntil"] = native_expiry.isoformat()
        event_admission_snapshot(projected, observed_at=observed_at)
    except (ValueError, OverflowError):
        raise ValueError("tushare_identity_not_admitted") from None
    return admission, recorded, now


def _unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("tushare_identity_duplicate_field")
        result[name] = value
    return result


def _invalid_constant(value):
    raise ValueError("tushare_identity_invalid_evidence")


@dataclass(frozen=True)
class TushareFundIdentity:
    query_fund_code: str
    currency: str
    evidence_ref: str
    evidence_sha256: str
    content: bytes


def resolve_tushare_fund_identity(
    content, admission, *, symbol, start, end, data_as_of, observed_at,
):
    """纯校验已审核关联；不读取文件、凭据或来源，不授予完整历史覆盖。"""
    if not isinstance(symbol, str) or _SYMBOL.fullmatch(symbol) is None or _date(start) > _date(end):
        raise ValueError("tushare_identity_invalid_scope")
    cutoff = _instant(data_as_of)
    current, recorded, now = _current_admission(admission, observed_at=observed_at, cutoff=cutoff)
    key = {"kind": "data", "market": "CN", "assetType": "ETF", "capability": "CASH_DISTRIBUTION"}
    if (current["routeKey"] != key
            or current["target"] != {"providerId": "tushare", "upstreamSource": "tushare"}
            or not route_admission_scope_applies(current, symbol=symbol, date_from=start, date_to=end)):
        raise ValueError("tushare_identity_not_admitted")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise ValueError("tushare_identity_invalid_evidence")
    digest = sha256(content).hexdigest()
    if current["evidenceRef"] != f"sha256:{digest}" or current["evidenceSha256"] != digest:
        raise ValueError("tushare_identity_digest_mismatch")
    try:
        bundle = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("tushare_identity_invalid_evidence") from None
    except ValueError as error:
        if str(error) in {"tushare_identity_duplicate_field", "tushare_identity_invalid_evidence"}:
            raise
        raise ValueError("tushare_identity_invalid_evidence") from None
    if (not isinstance(bundle, dict) or set(bundle) != {"contractVersion", "kind", "mappings"}
            or type(bundle["contractVersion"]) is not int or bundle["contractVersion"] != 1
            or bundle["kind"] != "tushare-fund-identity"
            or not isinstance(bundle["mappings"], list) or not 1 <= len(bundle["mappings"]) <= 1000):
        raise ValueError("tushare_identity_invalid_evidence")
    admitted_first, admitted_last = _date(current["scopeDateFrom"]), _date(current["scopeDateTo"])
    identities, selected = set(), None
    for item in bundle["mappings"]:
        if not isinstance(item, dict) or set(item) != _MAPPING_FIELDS:
            raise ValueError("tushare_identity_invalid_mapping")
        identity = item["symbol"]
        if (not isinstance(identity, str) or _SYMBOL.fullmatch(identity) is None or identity in identities
                or item["instrumentType"] != "ETF" or item["queryFundCode"] != identity):
            raise ValueError("tushare_identity_invalid_mapping")
        identities.add(identity)
        first, last = _date(item["scopeDateFrom"]), _date(item["scopeDateTo"])
        observation = _instant(item["observedAt"])
        if (first > last or identity not in current["scopeSymbols"] or first < admitted_first or last > admitted_last
                or observation > now or observation > recorded or observation > cutoff):
            raise ValueError("tushare_identity_invalid_scope")
        _document(item["identityEvidence"])
        _document(item["dividendCurrencyEvidence"], currency=True)
        if identity == symbol and first <= start and end <= last:
            selected = TushareFundIdentity(
                item["queryFundCode"], item["dividendCurrencyEvidence"]["currency"],
                current["evidenceRef"], digest, content,
            )
    if selected is None:
        raise ValueError("tushare_identity_missing_mapping")
    return selected
