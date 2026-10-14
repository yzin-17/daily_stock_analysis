"""AKShare 东方财富报价协议的 ETF 单标接入，显式绑定交易所。"""

from types import SimpleNamespace
import requests


def exact_etf_quote(symbol, error_type):
    code, exchange = symbol.rsplit(".", 1)
    if exchange not in {"SH", "SZ"} or len(code) != 6 or not code.isdigit():
        raise error_type("invalid_request", "ETF 报价需要完整沪深证券代码")
    market = 1 if exchange == "SH" else 0
    expected_prefixes = ("51", "52", "56", "58") if exchange == "SH" else ("15", "16", "18")
    if not code.startswith(expected_prefixes):
        raise error_type("invalid_request", "ETF 代码与交易所不匹配")
    try:
        response = requests.get("https://push2.eastmoney.com/api/qt/stock/get", params={
            "secid": f"{market}.{code}", "fltt": "2",
            "fields": "f13,f43,f44,f45,f46,f47,f48,f57,f58,f60",
        }, timeout=4.5)
        response.raise_for_status()
        data = response.json().get("data")
    except requests.RequestException:
        raise error_type("upstream_failure", "东方财富 ETF 报价请求失败") from None
    except (TypeError, ValueError, AttributeError):
        raise error_type("invalid_response", "东方财富 ETF 报价响应无效") from None
    if not isinstance(data, dict) or data.get("f57") != code or ("f13" in data and data["f13"] != market):
        raise error_type("invalid_response", "东方财富 ETF 报价标的身份不一致")
    return SimpleNamespace(price=data.get("f43"), open_price=data.get("f46"), high=data.get("f44"),
                           low=data.get("f45"), pre_close=data.get("f60"), volume=data.get("f47"),
                           amount=data.get("f48"))
