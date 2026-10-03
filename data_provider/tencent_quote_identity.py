"""腾讯单标的报价的响应封套与正文身份校验。"""

import re
from datetime import datetime
from zoneinfo import ZoneInfo


def verified_quote_fields(content: str, symbol: str) -> list[str]:
    if not re.fullmatch(r'(sh|sz|bj)[0-9]{6}', symbol):
        raise ValueError('unsupported_quote_identity')
    envelope = re.fullmatch(r'\s*v_' + re.escape(symbol) + r'\s*=\s*"([^"\r\n]*)";?\s*', content)
    if envelope is None:
        raise ValueError('quote_envelope_identity_mismatch')
    fields = envelope.group(1).split('~')
    if len(fields) < 45:
        raise ValueError('insufficient_quote_fields')
    if fields[2] != symbol[2:]:
        raise ValueError('quote_body_identity_mismatch')
    if not fields[1].strip():
        raise ValueError('quote_name_missing')
    return fields


def quote_timestamp(fields: list[str]) -> str | None:
    """保留来源的中国本地时间；缺失或无效时不伪造获取时间。"""
    value = fields[30] if len(fields) > 30 else ''
    if not re.fullmatch(r'[0-9]{14}', value):
        return None
    try:
        parsed = datetime.strptime(value, '%Y%m%d%H%M%S')
    except ValueError:
        return None
    return parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai')).isoformat()
