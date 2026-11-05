"""新闻合同的公共身份、时间与证据分组；不解析或执行原文指令。"""

from datetime import datetime
from hashlib import sha256
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def news_time(value: str) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value):
        raise ValueError("新闻时间必须使用带时区的 ISO 时间")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("新闻时间必须包含明确时区")
    return parsed


def news_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("新闻 URL 必须是无凭据的公共 HTTP(S) 地址")
    query = [(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.lower().startswith("utm_") and key.lower() != "spm"]
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path,
                       urlencode(sorted(query)), ""))


def news_item_id(provider_id: str, source_item_id: str | None, url: str) -> str:
    identity = source_item_id if source_item_id is not None else news_url(url)
    return "news-item-v1:" + sha256(f"{provider_id}:{identity}".encode()).hexdigest()


def news_evidence_group(original_url: str | None, fingerprint: str) -> str:
    identity = f"origin:{news_url(original_url)}" if original_url else f"content:{fingerprint}"
    return "news-evidence-v1:" + sha256(identity.encode()).hexdigest()


def validate_news_symbol(market: str, asset_type: str, symbol: str) -> None:
    if market == "CN":
        pattern = r"\d{6}\.OF" if asset_type == "MUTUAL_FUND" else r"\d{6}\.(SH|SZ|BJ)"
    elif market == "HK":
        pattern = r"[A-Z0-9^.-]+\.HK" if asset_type == "INDEX" else r"\d{5}\.HK"
    else:
        pattern = r"[A-Z^][A-Z0-9.-]*\.US"
    if not re.fullmatch(pattern, symbol):
        raise ValueError("新闻标的必须使用与市场一致的规范代码，不能推断同名或跨市场身份")
    if asset_type == "MUTUAL_FUND" and market != "CN":
        raise ValueError("当前基金新闻标识仅支持已定义的 CN 场外基金合同")
