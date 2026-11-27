"""Tushare 原有只读 HTTP 传输；保留结构化错误供来源解释。"""

import json
import re
from decimal import Decimal

import pandas as pd
import requests

from .tushare_endpoint import DEFAULT_TUSHARE_HTTP_URL

_frequency_limit = re.compile(r"每(分钟|小时|天|日|秒)最多访问该接口\s*([0-9]{1,9})\s*次")
_error_messages = {
    "authentication_failed": "Tushare 凭据认证失败",
    "permission_denied": "Tushare 接口权限或积分不足",
    "rate_limited": "Tushare 接口访问频率或额度受限",
    "rate_limited_global": "Tushare 请求频率受限",
    "invalid_response": "Tushare 响应格式无效",
    "upstream_failure": "Tushare 接口调用失败",
}


def _error_code(message, api_code, status_code):
    if status_code == 429:
        return "rate_limited_global"
    if status_code == 401:
        return "authentication_failed"
    if status_code is not None:
        return "upstream_failure"
    text = "".join(message.lower().split())
    if any(term in text for term in (
        "每分钟", "每天最多", "每日最多", "每小时", "每秒", "频率超限", "频率过高", "频次超限",
        "访问次数已满", "请求过于频繁", "ratelimit", "rate_limit", "toomanyrequests", "quota", "配额",
    )):
        return "rate_limited"
    if any(term in text for term in (
        "invalidtoken", "token无效", "token不对", "token错误", "token已过期", "token失效", "无效的token",
    )):
        return "authentication_failed"
    if api_code == 2002 or any(term in text for term in (
        "没有访问该接口的权限", "没有该接口的访问权限", "没有接口权限", "没有接口访问权限",
        "无接口访问权限", "暂无访问权限", "无访问权限", "没有访问权限", "权限不足",
        "没有权限", "无权访问", "积分不足", "permissiondenied", "insufficientpoints",
    )):
        return "permission_denied"
    return "upstream_failure"


class TushareApiError(Exception):
    def __init__(self, message="", *, api_code=None, status_code=None, code=None):
        self.api_code = api_code
        self.status_code = status_code
        self.code = code or _error_code(message, api_code, status_code)
        match = _frequency_limit.search(message) if self.code == "rate_limited" else None
        self.frequency_limit = match.groups() if match else None
        detail = f"（HTTP {status_code}）" if status_code is not None else ""
        super().__init__(_error_messages[self.code] + detail)


def _reject_non_json_number(_value):
    raise ValueError("non-JSON number")


def _read_frame(result):
    data = result.get("data")
    if not isinstance(data, dict):
        raise TushareApiError(code="invalid_response")
    fields, items = data.get("fields"), data.get("items")
    if (not isinstance(fields, list) or not isinstance(items, list)
            or any(not isinstance(field, str) or not field.strip() for field in fields)
            or len(fields) != len(set(fields))
            or any(not isinstance(row, list) or not fields or len(row) != len(fields) for row in items)):
        raise TushareApiError(code="invalid_response")
    return pd.DataFrame(items, columns=fields)


class _TushareHttpClient:
    def __init__(self, token: str, timeout: int = 30, api_url: str = DEFAULT_TUSHARE_HTTP_URL) -> None:
        self._token, self._timeout, self._api_url = token, timeout, api_url

    def query(self, api_name: str, fields: str = "", *, timeout_seconds=None, **kwargs) -> pd.DataFrame:
        request_options = {"timeout": self._timeout if timeout_seconds is None else timeout_seconds}
        if getattr(self, "_source_pinned", False):
            request_options["allow_redirects"] = False
        res = requests.post(self._api_url, json={
            "api_name": api_name, "token": self._token, "params": kwargs, "fields": fields,
        }, **request_options)
        if res.status_code != 200:
            raise TushareApiError(status_code=res.status_code)
        try:
            result = json.loads(res.text, parse_constant=_reject_non_json_number,
                                parse_float=Decimal if api_name in {"fund_adj", "fund_div"} else float)
        except (TypeError, ValueError, RecursionError):
            raise TushareApiError(code="invalid_response") from None
        if not isinstance(result, dict) or type(result.get("code")) is not int:
            raise TushareApiError(code="invalid_response")
        if result["code"] != 0:
            message = result.get("msg")
            raise TushareApiError(message if isinstance(message, str) else "", api_code=result["code"])
        return _read_frame(result)

    def __getattr__(self, api_name: str):
        if api_name.startswith("_"):
            raise AttributeError(api_name)
        return lambda **kwargs: self.query(api_name, **kwargs)
