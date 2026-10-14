"""Tushare 原有只读 HTTP 传输；保留结构化错误供来源解释。"""

import json
from decimal import Decimal

import pandas as pd
import requests


class TushareApiError(Exception):
    def __init__(self, message, *, api_code=None, status_code=None):
        super().__init__(message)
        self.api_code = api_code
        self.status_code = status_code


class _TushareHttpClient:
    def __init__(self, token: str, timeout: int = 30, api_url: str = "http://api.tushare.pro") -> None:
        self._token, self._timeout, self._api_url = token, timeout, api_url

    def query(self, api_name: str, fields: str = "", *, timeout_seconds=None, **kwargs) -> pd.DataFrame:
        request_options = {"timeout": self._timeout if timeout_seconds is None else timeout_seconds}
        if getattr(self, "_source_pinned", False):
            request_options["allow_redirects"] = False
        res = requests.post(self._api_url, json={
            "api_name": api_name, "token": self._token, "params": kwargs, "fields": fields,
        }, **request_options)
        if res.status_code != 200:
            raise TushareApiError(f"Tushare API HTTP {res.status_code}", status_code=res.status_code)
        result = json.loads(res.text, parse_float=Decimal if api_name in {"fund_adj", "fund_div"} else float)
        if not isinstance(result, dict):
            raise ValueError("Tushare API 响应形状无效")
        if timeout_seconds is not None and type(result.get("code")) is not int:
            raise ValueError("Tushare API 响应码无效")
        if result.get("code") != 0:
            raise TushareApiError(result.get("msg") or f"Tushare API error code {result.get('code')}",
                                  api_code=result.get("code"))
        data = result.get("data") or {}
        columns, items = data.get("fields") or [], data.get("items") or []
        # 账户检查要求成功响应的业务信封确实存在；空列表仍仅代表接口接受请求。
        if timeout_seconds is not None and (not isinstance(result.get("data"), dict)
                or not isinstance(data.get("fields"), list) or not isinstance(data.get("items"), list)):
            raise ValueError("Tushare API 成功响应缺少业务字段")
        return pd.DataFrame(items, columns=columns)

    def __getattr__(self, api_name: str):
        if api_name.startswith("_"):
            raise AttributeError(api_name)
        return lambda **kwargs: self.query(api_name, **kwargs)
