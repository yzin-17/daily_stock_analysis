"""新浪指数快照的完整代码身份选择，不推断来源时点或准入。"""

from typing import Optional

import pandas as pd


def select_unique_sina_index_row(
    frame: pd.DataFrame, code: str
) -> Optional[pd.Series]:
    """只返回完整代码唯一精确匹配的原始行，身份不明确时拒绝。"""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if list(frame.columns).count("代码") != 1:
        return None
    matches = frame[frame["代码"].map(
        lambda value: isinstance(value, str) and value == code
    )]
    if len(matches) != 1:
        return None
    return matches.iloc[0]
