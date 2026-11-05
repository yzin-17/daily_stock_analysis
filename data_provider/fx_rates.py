"""既有 Yahoo 汇率查询适配；不读取组合业务或配置。"""

from datetime import date, timedelta
from typing import Any


def fetch_fx_rate_from_yfinance(
    *, from_currency: str, to_currency: str, as_of_date: date, sdk: Any = None,
) -> float | None:
    """保留请求窗口、未复权日线与最后有效收盘值的现行合同。"""
    if sdk is None:
        try:
            import yfinance as sdk
        except Exception:
            return None
    history = sdk.Ticker(f"{from_currency}{to_currency}=X").history(
        start=(as_of_date - timedelta(days=7)).isoformat(),
        end=(as_of_date + timedelta(days=1)).isoformat(),
        interval="1d", auto_adjust=False,
    )
    if history is None or history.empty or "Close" not in history:
        return None
    close = history["Close"].dropna()
    if close.empty:
        return None
    value = float(close.iloc[-1])
    if value <= 0:
        return None
    return value
