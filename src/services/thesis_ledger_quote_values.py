"""报价的非必需数值：未知保留为空，不以零值冒充来源事实。"""

from __future__ import annotations

import math
from typing import Any


def optional_quote_number(value: Any, *, signed: bool = False) -> float | None:
    """仅供非必需报价字段使用；价格、身份和时间继续由原合同验证。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or (not signed and number < 0):
        return None
    return number
