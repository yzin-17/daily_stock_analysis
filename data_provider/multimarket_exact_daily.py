"""多市场日线精确请求；保留来源价格语义和缺失字段。"""

import requests

from .exact_daily_contract import ExactDailyError


def _http_json(url, params):
    try:
        response = requests.get(url, params=params, timeout=4.5)
        response.raise_for_status()
        value = response.json()
    except requests.HTTPError as error:
        status = getattr(error.response, "status_code", None)
        if status in {401, 403}:
            raise ExactDailyError("permission_denied", "来源凭证或接口权限不足") from None
        if status == 429:
            raise ExactDailyError("quota_unavailable", "来源调用额度暂不可用") from None
        raise ExactDailyError("upstream_failure", "来源日线请求失败") from None
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
