# -*- coding: utf-8 -*-
"""Regression tests for TushareFetcher HTTP client initialization."""

import importlib.util
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.tushare_fetcher import (
    TushareFetcher,
    _TushareHttpClient,
    _resolve_tushare_http_url,
)
from data_provider.tushare_account_access import probe_account_access
from data_provider.tushare_http_client import TushareApiError
from src.services.thesis_ledger_provider_errors import classify_provider_failure


def envelope(code=0, *, msg=None, data=None):
    if data is None and code == 0:
        data = {"fields": ["ts_code"], "items": []}
    return SimpleNamespace(status_code=200, text=json.dumps({"code": code, "msg": msg, "data": data}))


def test_account_probe_and_data_read_use_their_own_responses(monkeypatch):
    token = "fixture-token"
    client = _TushareHttpClient(token)
    context = SimpleNamespace(key={"market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR"},
                              target={"upstreamSource": "tushare"}, timeout_seconds=1.5,
                              adapter=SimpleNamespace(_api=_TushareHttpClient(token)))
    post = MagicMock(side_effect=[envelope(2002), envelope()])
    monkeypatch.setattr("data_provider.tushare_http_client.requests.post", post)
    assert probe_account_access(context) == {
        "state": "permission_missing", "attempted": True, "errorCode": "permission_denied",
    }
    assert post.call_args.kwargs["timeout"] == 1.5
    assert post.call_args.kwargs["json"]["params"] == {"ts_code": "600519.SH", "trade_date": "20250115"}
    assert client.daily().empty and post.call_count == 2


@pytest.mark.parametrize("reply,code", [
    (envelope(2002, msg="权限不足 fixture-token"), "permission_denied"),
    (envelope(2002, msg="每分钟最多访问该接口 80 次，权限说明"), "rate_limited"),
    (envelope(2002, msg="访问频率过高，请查看接口权限说明"), "rate_limited"),
    (envelope(2002, msg="token不对"), "authentication_failed"),
    (envelope(40203, msg="抱歉，您没有接口(cyq_chips)访问权限，权限的具体详情访问：https://tushare.pro/document/1?doc_id=108。"), "permission_denied"),
    (envelope(40203, msg="未知错误"), "upstream_failure"),
    (SimpleNamespace(status_code=401), "authentication_failed"),
    (SimpleNamespace(status_code=403), "upstream_failure"),
    (SimpleNamespace(status_code=429), "rate_limited_global"),
    (SimpleNamespace(status_code=503), "upstream_failure"),
])
def test_each_request_uses_current_upstream_response(monkeypatch, reply, code):
    client = _TushareHttpClient("fixture-token")
    post = MagicMock(side_effect=[reply, envelope()])
    monkeypatch.setattr("data_provider.tushare_http_client.requests.post", post)
    with pytest.raises(TushareApiError) as error:
        client.daily()
    assert error.value.code == code
    assert "fixture-token" not in str(error.value)
    runtime_code = "permission_denied" if error.value.status_code == 403 else code
    assert classify_provider_failure(error.value) == runtime_code
    assert client.daily().empty and post.call_count == 2


def test_network_failure_never_blocks_the_next_request(monkeypatch):
    client = _TushareHttpClient("fixture-token")
    post = MagicMock(side_effect=[requests.Timeout(), envelope()])
    monkeypatch.setattr("data_provider.tushare_http_client.requests.post", post)
    with pytest.raises(requests.Timeout):
        client.daily()
    assert client.daily().empty and post.call_count == 2


@pytest.mark.parametrize("wire", [
    "<html>upstream error</html>",
    "[]",
    '{"code": true, "data": {"fields": [], "items": []}}',
    '{"code": "0", "data": {"fields": [], "items": []}}',
    '{"code": 0}',
    '{"code": 0, "data": []}',
    '{"code": 0, "data": {"fields": ["x"]}}',
    '{"code": 0, "data": {"fields": ["x", "x"], "items": []}}',
    '{"code": 0, "data": {"fields": ["x", 1], "items": []}}',
    '{"code": 0, "data": {"fields": ["x"], "items": {"x": 1}}}',
    '{"code": 0, "data": {"fields": ["x"], "items": [{"x": 1}]}}',
    '{"code": 0, "data": {"fields": ["x"], "items": [[1, 2]]}}',
    '{"code": 0, "data": {"fields": ["x", "y"], "items": [[1]]}}',
    '{"code": 0, "data": {"fields": ["x"], "items": [[NaN]]}}',
])
def test_malformed_wire_envelope_is_not_accepted_as_empty(monkeypatch, wire):
    client = _TushareHttpClient("fixture-token")
    post = MagicMock(side_effect=[SimpleNamespace(status_code=200, text=wire), envelope()])
    monkeypatch.setattr("data_provider.tushare_http_client.requests.post", post)
    with pytest.raises(TushareApiError) as error:
        client.daily()
    assert error.value.code == "invalid_response"
    assert client.daily().empty and post.call_count == 2


class TestTushareHttpClient(unittest.TestCase):
    """Ensure the lightweight HTTP client preserves Tushare Pro request semantics."""

    def test_query_posts_to_official_pro_endpoint(self) -> None:
        client = _TushareHttpClient(token="demo-token", timeout=15)
        response = MagicMock(
            status_code=200,
            text=json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "close"],
                        "items": [["600519.SH", 1688.0]],
                    },
                }
            ),
        )

        with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post_mock:
            df = client.daily(ts_code="600519.SH", start_date="20260320", end_date="20260325")

        post_mock.assert_called_once_with(
            "http://api.tushare.pro",
            json={
                "api_name": "daily",
                "token": "demo-token",
                "params": {
                    "ts_code": "600519.SH",
                    "start_date": "20260320",
                    "end_date": "20260325",
                },
                "fields": "",
            },
            timeout=15,
        )
        self.assertEqual(df.to_dict(orient="records"), [{"ts_code": "600519.SH", "close": 1688.0}])


class TestTushareFetcherInit(unittest.TestCase):
    """Ensure fetcher initialization no longer depends on the tushare SDK package."""

    def test_init_builds_http_client_when_token_present(self) -> None:
        config = SimpleNamespace(tushare_token="demo-token")

        with patch("data_provider.tushare_fetcher.get_config", return_value=config):
            fetcher = TushareFetcher()

        self.assertIsInstance(fetcher._api, _TushareHttpClient)
        self.assertTrue(fetcher.is_available())
        self.assertEqual(fetcher.priority, -1)


class TestResolveTushareHttpUrl(unittest.TestCase):
    """``TUSHARE_HTTP_URL`` 环境变量解析与校验。"""

    def test_unset_returns_none(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("TUSHARE_HTTP_URL", None)
            self.assertIsNone(_resolve_tushare_http_url())

    def test_empty_or_whitespace_returns_none(self) -> None:
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "   "}):
            self.assertIsNone(_resolve_tushare_http_url())

    def test_valid_http_url_returned_stripped(self) -> None:
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "  http://gw.example.com/tushare  "}):
            self.assertEqual(_resolve_tushare_http_url(), "http://gw.example.com/tushare")

    def test_https_url_returned(self) -> None:
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "https://gw.example.com/tushare"}):
            self.assertEqual(_resolve_tushare_http_url(), "https://gw.example.com/tushare")

    def test_missing_schema_raises_value_error(self) -> None:
        # 防止有人误填纯主机名（如 'api.tushare.pro'）后被 requests 当成相对路径
        with patch.dict("os.environ", {"TUSHARE_HTTP_URL": "gw.example.com"}):
            with self.assertRaises(ValueError):
                _resolve_tushare_http_url()


class TestTushareFetcherCustomHttpUrl(unittest.TestCase):
    """``TUSHARE_HTTP_URL`` 真正打通到 HTTP client 的接入地址。"""

    def test_fetcher_uses_custom_url_when_env_set(self) -> None:
        config = SimpleNamespace(tushare_token="demo-token")

        with patch("data_provider.tushare_fetcher.get_config", return_value=config), \
                patch.dict("os.environ", {"TUSHARE_HTTP_URL": "http://gw.example.com/tushare"}):
            fetcher = TushareFetcher()

        self.assertIsInstance(fetcher._api, _TushareHttpClient)
        self.assertEqual(fetcher._api._api_url, "http://gw.example.com/tushare")

    def test_fetcher_falls_back_to_official_when_env_empty(self) -> None:
        config = SimpleNamespace(tushare_token="demo-token")

        env = {k: v for k, v in __import__("os").environ.items() if k != "TUSHARE_HTTP_URL"}
        with patch("data_provider.tushare_fetcher.get_config", return_value=config), \
                patch.dict("os.environ", env, clear=True):
            fetcher = TushareFetcher()

        self.assertIsInstance(fetcher._api, _TushareHttpClient)
        self.assertEqual(fetcher._api._api_url, "http://api.tushare.pro")

    def test_query_posts_to_custom_endpoint(self) -> None:
        """端到端确保自定义 url 真正驱动 requests.post 的目标地址。"""
        config = SimpleNamespace(tushare_token="demo-token")

        with patch("data_provider.tushare_fetcher.get_config", return_value=config), \
                patch.dict("os.environ", {"TUSHARE_HTTP_URL": "http://gw.example.com/tushare"}):
            fetcher = TushareFetcher()

        response = MagicMock(
            status_code=200,
            text=json.dumps(
                {
                    "code": 0,
                    "data": {
                        "fields": ["ts_code", "close"],
                        "items": [["600519.SH", 1688.0]],
                    },
                }
            ),
        )

        with patch("data_provider.tushare_fetcher.requests.post", return_value=response) as post_mock:
            fetcher._api.daily(ts_code="600519.SH", start_date="20260320", end_date="20260325")

        post_mock.assert_called_once_with(
            "http://gw.example.com/tushare",
            json={
                "api_name": "daily",
                "token": "demo-token",
                "params": {
                    "ts_code": "600519.SH",
                    "start_date": "20260320",
                    "end_date": "20260325",
                },
                "fields": "",
            },
            timeout=30,
        )


if __name__ == "__main__":
    unittest.main()
