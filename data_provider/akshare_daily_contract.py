"""精确东财日线的原生字段契约；未知单位不由其他资产推断。"""

from typing import Any


def annotate_exact_daily_contract(frame: Any, instrument_kind: str, source: str, adjustment: str) -> Any:
    if source != "eastmoney":
        return frame
    stock = instrument_kind == "stock"
    frame.attrs["native_daily_contract"] = {
        "contractVersion": "akshare-eastmoney-native-fields-v1",
        "providerId": "akshare",
        "upstreamSource": "eastmoney",
        "independentSourceId": "eastmoney",
        "endpoint": "stock_zh_a_hist" if stock else "fund_etf_hist_em",
        "assetType": "STOCK" if stock else "ETF",
        "adjustment": adjustment,
        "nativeAdjust": "" if adjustment == "none" else adjustment,
        "volumeUnit": "hand" if stock else "unknown",
        "amountUnit": "CNY" if stock else "unknown",
        "volumeAdjustment": "unknown",
        "valuesConverted": False,
        "documentation": (
            "https://akshare.akfamily.xyz/data/stock/stock.html"
            if stock else "https://akshare.akfamily.xyz/data/fund/fund_public.html"
        ),
    }
    return frame
