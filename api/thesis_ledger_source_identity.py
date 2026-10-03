"""ThesisLedger HTTP 来源身份的稳定归一化。"""

from __future__ import annotations

from typing import Any


def upstream_source(value: Any) -> str | None:
    """只归一已确认的适配器别名；未知来源不伪造身份。"""
    raw = getattr(value, "value", value)
    normalized = str(raw or "").strip().lower()
    aliases = {
        "eastmoney": "eastmoney", "akshare_em": "eastmoney", "efinance": "eastmoney",
        "sina": "sina", "akshare_sina": "sina",
        "akshare_qq": "tencent", "tencent": "tencent",
        "hithink/a-share-prices-snapshot": "a-share-prices-snapshot",
        "hithink/fund-market-snapshot": "fund-market-snapshot",
    }
    return aliases.get(normalized)
