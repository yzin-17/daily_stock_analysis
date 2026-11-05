"""数据配置调用方：真实环境解析、产品导入阻断和内部失败区分。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.data_config import DataConfigurationError
from src.services.thesis_ledger_environment_credentials import environment_credential_values


CONSUMER_PROBE = r"""
import base64, importlib.abc, json, socket, sys
forbidden = (
    "src.config", "src.llm", "src.agent", "src.scheduler", "src.notification",
    "src.report_language", "src.schemas.decision_scale", "litellm", "openai",
    "google.genai", "google.generativeai",
)
class BlockProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("data consumer imported a product or model SDK: " + fullname)
sys.meta_path.insert(0, BlockProducts())
attempts = []
def deny(self, address):
    attempts.append("connection")
    raise AssertionError("data credential read attempted a network connection")
socket.socket.connect = deny
from src.services.thesis_ledger_environment_credentials import environment_credential_values
from src.services.provider_credentials import CredentialPatch, encode_credential_plaintext, decode_credential_plaintext
values = {provider: environment_credential_values(provider) for provider in
          ("tushare", "tickflow", "finnhub", "alphavantage", "longbridge", "unknown")}
encoded = encode_credential_plaintext(CredentialPatch("api_key", values["tickflow"]))
assert decode_credential_plaintext(encoded).values == values["tickflow"]
assert not attempts
assert not any(name == p or name.startswith(p + ".") for name in sys.modules for p in forbidden)
print("__CONSUMER_RESULT__" + json.dumps(values))
"""


@pytest.mark.parametrize("method", ["missing", "legacy", "oauth"])
def test_real_credential_read_without_product_modules_or_model_sdks(tmp_path, method):
    token = json.dumps({"client_id": "fixture-client", "access_token": "fixture-access",
                        "refresh_token": "fixture-refresh", "expires_at": 1900000000})
    values = {}
    expected = {provider: {} for provider in
                ("tushare", "tickflow", "finnhub", "alphavantage", "longbridge", "unknown")}
    if method != "missing":
        values = {"TUSHARE_TOKEN": "fixture-tushare", "TICKFLOW_API_KEY": "fixture-tickflow",
                  "FINNHUB_API_KEY": "fixture-finnhub", "ALPHAVANTAGE_API_KEY": "fixture-alpha"}
        expected.update(tushare={"token": "fixture-tushare"}, tickflow={"apiKey": "fixture-tickflow"},
                        finnhub={"apiKey": "fixture-finnhub"}, alphavantage={"apiKey": "fixture-alpha"})
    if method == "legacy":
        values.update(LONGBRIDGE_APP_KEY="fixture-key", LONGBRIDGE_APP_SECRET="fixture-secret",
                      LONGBRIDGE_ACCESS_TOKEN="fixture-access")
        expected["longbridge"] = {"appKey": "fixture-key", "appSecret": "fixture-secret",
                                  "accessToken": "fixture-access"}
    elif method == "oauth":
        import base64
        values.update(LONGBRIDGE_OAUTH_CLIENT_ID="fixture-client",
                      LONGBRIDGE_OAUTH_TOKEN_CACHE_B64=base64.b64encode(token.encode()).decode())
        expected["longbridge"] = {"clientId": "fixture-client", "tokenJson": token}
    env_file = tmp_path / "data.env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()))
    result = subprocess.run(
        [sys.executable, "-c", CONSUMER_PROBE], cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "ENV_FILE": str(env_file)},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr[-1600:]
    assert json.loads(result.stdout.split("__CONSUMER_RESULT__")[-1]) == expected


def test_unexpected_loader_failure_does_not_turn_into_empty_credentials(monkeypatch, caplog):
    def fail():
        raise RuntimeError("fixture-sensitive-canary")
    monkeypatch.setattr("src.data_config.get_data_config", fail)
    with pytest.raises(DataConfigurationError, match="数据凭据配置读取失败") as captured:
        environment_credential_values("tushare")
    assert captured.value.__suppress_context__
    assert "fixture-sensitive-canary" not in str(captured.value)
    assert "fixture-sensitive-canary" not in caplog.text


@pytest.mark.parametrize("raw,expected", [
    (None, "zh"), ("zh-Hant", "zh"), ("zh CN", "zh"),
    (" English ", "en"), ("en GB", "en"), ("ko-KR", None), ("bad", "zh"),
])
def test_longbridge_sdk_language_preserves_existing_parameter_meaning(raw, expected, monkeypatch):
    from types import ModuleType, SimpleNamespace
    from data_provider.longbridge_fetcher import _longbridge_config_kwargs

    class Config:
        @staticmethod
        def from_apikey(*, language=None):
            raise AssertionError("语言参数解析不应建立 SDK 连接")

    sdk = ModuleType("longbridge.openapi")
    sdk.Config = Config
    sdk.Language = SimpleNamespace(ZH_CN="zh", EN="en")
    sdk.PushCandlestickMode = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "longbridge.openapi", sdk)
    if raw is None:
        monkeypatch.delenv("REPORT_LANGUAGE", raising=False)
    else:
        monkeypatch.setenv("REPORT_LANGUAGE", raw)
    kwargs = _longbridge_config_kwargs()
    if expected is None:
        assert "language" not in kwargs
    else:
        assert kwargs == {"language": expected}


def test_optional_fetchers_do_not_hide_configuration_failure(monkeypatch):
    from data_provider.longbridge_fetcher import LongbridgeFetcher
    from data_provider.tickflow_fetcher import TickFlowFetcher

    def fail():
        raise DataConfigurationError("数据配置文件加载失败")
    monkeypatch.setattr("src.data_config.get_data_config", fail)
    with pytest.raises(DataConfigurationError):
        LongbridgeFetcher()._is_available()
    with pytest.raises(DataConfigurationError):
        TickFlowFetcher._get_realtime_cache_ttl()


@pytest.mark.parametrize("provider", ["tushare", "tickflow", "finnhub", "alphavantage", "longbridge"])
def test_actual_config_file_failure_is_not_missing_credential(tmp_path, monkeypatch, caplog, provider):
    from src.data_config import reset_data_config
    with monkeypatch.context() as isolated:
        isolated.setattr(os, "environ", {"ENV_FILE": str(tmp_path)})
        reset_data_config()
        try:
            with pytest.raises(DataConfigurationError, match="配置路径不是文件"):
                environment_credential_values(provider)
            assert str(tmp_path) not in caplog.text
        finally:
            reset_data_config()
