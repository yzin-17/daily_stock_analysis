"""ThesisLedger HTTP 来源身份的稳定归一化。"""

from __future__ import annotations

from typing import Any
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


def upstream_source(value: Any, provider_id: str) -> str | None:
    """只归一已确认的适配器别名；未知来源不伪造身份。"""
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip().lower()
    source = CAPABILITY_SOURCES.source(provider_id)
    return dict(source.quote_source_aliases).get(normalized) if source is not None else None
