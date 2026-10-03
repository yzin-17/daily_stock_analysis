"""易方达已审查净值披露规则；原文变化或未经审查范围均不可用。"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import time
from urllib.parse import urlsplit

import requests


@dataclass(frozen=True)
class EfundsNavRuleSpec:
    symbol: str
    version: str
    url: str
    document_hash: str
    delay_workdays: int
    audited_start: str
    audited_end: str
    pages: tuple[int, ...]


EFUNDS_NAV_RULES = {
    '110011.OF': EfundsNavRuleSpec(
        '110011.OF', 'efunds-110011-20260701-audit-20260930',
        'https://cdn.efunds.com.cn/owch/data/bulletin/20260701/易方达优质精选混合型证券投资基金更新的招募说明书.pdf',
        'bc6940d7f3209ff05571e20727200d69ca78ffce593d6a6921fb0ec521c4f1c4',
        1, '2026-07-01', '2026-09-30', (8, 55, 70),
    ),
    '118001.OF': EfundsNavRuleSpec(
        '118001.OF', 'efunds-118001-20260727-audit-20260930',
        'https://cdn.efunds.com.cn/owch/data/bulletin/20260727/易方达亚洲精选股票型证券投资基金更新的招募说明书.pdf',
        '2c0ac2a0eb177f9966a7dd254e2bdf29c269654baf9c489928898068594d068b',
        2, '2026-07-27', '2026-09-30', (11, 55, 79),
    ),
}
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
READER_REVISION = 'efunds-nav-disclosure-document-v1'


@dataclass(frozen=True)
class VerifiedNavDocument:
    raw: bytes
    content_hash: str
    captured_at: str
    reader_revision: str


def read_verified_nav_document(spec: EfundsNavRuleSpec) -> VerifiedNavDocument:
    parsed = urlsplit(spec.url)
    if (parsed.scheme != 'https' or parsed.netloc != 'cdn.efunds.com.cn'
            or parsed.query or parsed.fragment or not parsed.path.endswith('.pdf')):
        raise ValueError('净值规则文档地址不在已审查来源内')
    deadline = time.monotonic() + 30
    chunks, size = [], 0
    with requests.get(spec.url, timeout=(5, 10), verify=True, stream=True,
                      allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('净值规则文档响应状态无效')
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > MAX_DOCUMENT_BYTES or time.monotonic() >= deadline:
                raise ValueError('净值规则文档读取预算耗尽')
            chunks.append(chunk)
    raw = b''.join(chunks)
    digest = sha256(raw).hexdigest()
    if not raw.startswith(b'%PDF-') or digest != spec.document_hash:
        raise ValueError('净值规则文档原文与已审查摘要不符')
    if time.monotonic() >= deadline:
        raise TimeoutError('净值规则文档读取预算耗尽')
    return VerifiedNavDocument(raw, digest, datetime.now(timezone.utc).isoformat(), READER_REVISION)
