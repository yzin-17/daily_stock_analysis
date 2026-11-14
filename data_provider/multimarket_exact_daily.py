"""多市场日线精确请求；保留来源价格语义和缺失字段。"""

import requests

from .exact_daily_contract import ExactDailyError
from src.services.thesis_ledger_provider_errors import classify_provider_failure


def _http_json(url, params):
    try:
        response = requests.get(url, params=params, timeout=4.5)
        response.raise_for_status()
        value = response.json()
    except requests.HTTPError as error:
        raise ExactDailyError(classify_provider_failure(error), "来源请求未完成，请核对权限或调用限制") from None
    except (requests.RequestException, ValueError):
        raise ExactDailyError("upstream_failure", "来源日线请求失败") from None
    if not isinstance(value, dict):
        raise ExactDailyError("invalid_response", "来源日线响应格式无效")
    return value


def read_multimarket_exact_daily(provider_id, adapter, request, market):
    if provider_id == "yfinance":
        from .thesis_ledger_sources.yfinance import read_frame
        return read_frame(adapter, request, market)
    elif provider_id == "longbridge":
        from .thesis_ledger_sources.longbridge import read_frame
        return read_frame(adapter, request, market)
    elif provider_id == "tushare":
        from .thesis_ledger_sources.tushare import read_frame
        return read_frame(adapter, request, market)
    elif provider_id == "finnhub":
        from .thesis_ledger_sources.finnhub import read_frame
        return read_frame(adapter, request)
    elif provider_id == "alphavantage":
        from .thesis_ledger_sources.alphavantage import read_frame
        return read_frame(adapter, request)
    else:
        raise ExactDailyError("not_adapted", "来源未登记多市场日线")
