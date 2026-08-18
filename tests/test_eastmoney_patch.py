"""东方财富 Cookie 注入与 NID 合并的确定性回归。"""

import logging
import sys
import types

import requests

import src.patches.eastmoney_patch as eastmoney_patch_module


def _install_efinance_config(monkeypatch, headers):
    config_module = types.ModuleType("efinance.common.config")
    config_module.EASTMONEY_REQUEST_HEADERS = headers
    common_module = types.ModuleType("efinance.common")
    common_module.config = config_module
    efinance_module = types.ModuleType("efinance")
    efinance_module.common = common_module
    monkeypatch.setitem(sys.modules, "efinance", efinance_module)
    monkeypatch.setitem(sys.modules, "efinance.common", common_module)
    monkeypatch.setitem(sys.modules, "efinance.common.config", config_module)


def test_configure_eastmoney_cookie_merges_existing_header_without_logging_secret(
    monkeypatch, caplog
) -> None:
    secret_cookie = "ct=local-session-cookie"
    headers = {"User-Agent": "test", "Cookie": "nid18=existing-nid"}
    _install_efinance_config(monkeypatch, headers)
    monkeypatch.setenv("EFINANCE_EASTMONEY_COOKIE", secret_cookie)

    with caplog.at_level(logging.DEBUG):
        assert eastmoney_patch_module.configure_eastmoney_cookie() is True

    assert headers["Cookie"] == "nid18=existing-nid; ct=local-session-cookie"
    assert secret_cookie not in caplog.text


def test_configure_eastmoney_cookie_rejects_header_injection(monkeypatch, caplog) -> None:
    headers = {"User-Agent": "test"}
    _install_efinance_config(monkeypatch, headers)
    monkeypatch.setenv("EFINANCE_EASTMONEY_COOKIE", "ct=bad\r\nX-Leak: value")

    with caplog.at_level(logging.WARNING):
        assert eastmoney_patch_module.configure_eastmoney_cookie() is False

    assert "Cookie" not in headers
    assert "X-Leak" not in caplog.text


def test_eastmoney_patch_keeps_existing_cookie_when_adding_nid(monkeypatch) -> None:
    captured = {}
    previous_request = requests.Session.request

    def fake_request(self, method, url, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(eastmoney_patch_module, "original_request", fake_request)
    monkeypatch.setattr(
        eastmoney_patch_module,
        "ua",
        types.SimpleNamespace(random="test-user-agent"),
    )
    monkeypatch.setattr(eastmoney_patch_module, "_get_nid", lambda _ua: "fresh-nid")
    monkeypatch.delenv("EFINANCE_EASTMONEY_COOKIE", raising=False)
    eastmoney_patch_module._patch_sign.set_patch(False)

    try:
        eastmoney_patch_module.eastmoney_patch()
        requests.Session().request(
            "GET",
            "https://push2his.eastmoney.com/api/qt/stock/kline/get",
            headers={"Cookie": "ct=logged-in"},
        )
    finally:
        requests.Session.request = previous_request
        eastmoney_patch_module._patch_sign.set_patch(False)

    assert captured["headers"]["Cookie"] == "ct=logged-in; nid18=fresh-nid"
