"""东财快照仅允许消费唯一精确代码的原行。"""

import re

import pandas as pd


def unique_quote_row(frame: pd.DataFrame, stock_code: str) -> pd.Series | None:
    if not isinstance(stock_code, str) or re.fullmatch(r'[0-9]{6}', stock_code) is None:
        return None
    if frame.empty or list(frame.columns).count('代码') != 1:
        return None
    matches = frame['代码'].map(lambda code: isinstance(code, str) and code == stock_code)
    rows = frame.loc[matches]
    if len(rows) != 1:
        return None
    return rows.iloc[0]
