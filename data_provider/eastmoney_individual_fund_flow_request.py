"""Explicit request scope for EastMoney individual fund flow."""

import re
from typing import Dict, Optional


def resolve_individual_fund_flow_request(stock_code: str) -> Optional[Dict[str, str]]:
    """Accept only an ASCII six-digit symbol with an explicit SH/SZ suffix."""
    if not isinstance(stock_code, str):
        return None
    matched = re.fullmatch(r"([0-9]{6})\.(SH|SZ)", stock_code, flags=re.ASCII)
    if matched is None:
        return None
    return {"stock": matched.group(1), "market": matched.group(2).lower()}
