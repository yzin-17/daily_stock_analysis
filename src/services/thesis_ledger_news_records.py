"""原始新闻结果的内部数据合同；外部协议仍使用 news-search-v1。"""

from dataclasses import dataclass

from src.services.thesis_ledger_news_contract_v1 import NewsError


@dataclass(frozen=True)
class RawNewsRecord:
    title: str
    url: str
    excerpt: str | None = None
    publisher: str | None = None
    source_item_id: str | None = None
    original_url: str | None = None
    published_at: str | None = None
    source_available_at: str | None = None


@dataclass(frozen=True)
class RawNewsRead:
    records: tuple[RawNewsRecord, ...] = ()
    success: bool = False
    upstream_attempts: int = 0
    errors: tuple[NewsError, ...] = ()
    truncated: bool = False
    next_cursor: str | None = None
    examined_count: int | None = None
    missing_reasons: tuple[str, ...] = ()
