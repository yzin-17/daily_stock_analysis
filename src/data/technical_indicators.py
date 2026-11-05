"""正式数据与原生趋势分析共用的纯指标内核；保留既有计算口径。"""

from collections.abc import Iterable

import pandas as pd


def calculate_moving_averages(frame: pd.DataFrame) -> pd.DataFrame:
    """保留四组均线及短序列的既有 MA60 回退。"""
    result = frame.copy()
    result["MA5"] = result["close"].rolling(window=5).mean()
    result["MA10"] = result["close"].rolling(window=10).mean()
    result["MA20"] = result["close"].rolling(window=20).mean()
    if len(result) >= 60:
        result["MA60"] = result["close"].rolling(window=60).mean()
    else:
        result["MA60"] = result["MA20"]
    return result


def calculate_macd(
    frame: pd.DataFrame, *, fast: int = 12, slow: int = 26, signal: int = 9,
) -> pd.DataFrame:
    """保留 adjust=False 的 EMA 种子和二倍柱值。"""
    result = frame.copy()
    ema_fast = result["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = result["close"].ewm(span=slow, adjust=False).mean()
    result["MACD_DIF"] = ema_fast - ema_slow
    result["MACD_DEA"] = result["MACD_DIF"].ewm(span=signal, adjust=False).mean()
    result["MACD_BAR"] = (result["MACD_DIF"] - result["MACD_DEA"]) * 2
    return result


def calculate_rsi(
    frame: pd.DataFrame, *, periods: Iterable[int] = (6, 12, 24),
) -> pd.DataFrame:
    """保留 Wilder EMA、缺值处理及中性值 50 的既有行为。"""
    result = frame.copy()
    for period in periods:
        delta = result["close"].diff()
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        result[f"RSI_{period}"] = rsi.fillna(50)
    return result
