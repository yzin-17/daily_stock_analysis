"""独立数据配置：固定旧解析基线、隔离导入和秘密保护。"""

import json
import math
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.data_config import (
    DataConfigurationError,
    DataServiceConfig,
    configure_data_proxy_environment,
    get_data_config,
    reset_data_config,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/data_config_legacy_v1.json").read_text()
)


def test_sample_config_loads_data_settings_without_live_credentials():
    from dotenv import dotenv_values

    values = dotenv_values(Path(__file__).parents[1] / ".env.example")
    config = DataServiceConfig.from_env(values)
    assert values["THESIS_LEDGER_FIXTURE_MODE"] == "false"
    assert config.database_path == "./data/stock_analysis.db"
    assert config.tickflow_batch_size == 100
    assert config.get_effective_news_window_days() == 3
    assert not config.stock_index_remote_update_enabled
    assert not config.searxng_public_instances_enabled
    assert not config.longbridge_app_key
    assert not config.longbridge_access_token
    assert not config.tavily_api_keys


def encode(value):
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            name = "nan"
        elif value > 0:
            name = "inf"
        else:
            name = "-inf"
        return {"__float__": name}
    return value


@pytest.mark.parametrize("case", FIXTURE["scenarios"], ids=lambda case: case["name"])
def test_explicit_values_match_pre_extraction_legacy(case, monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "outside-values-must-not-win")
    values = {**case["dotenv"], **case["environment"]}
    actual = DataServiceConfig.from_env(values)
    assert {name: encode(getattr(actual, name)) for name in FIXTURE["fields"]} == case["expected"]


ISOLATED_PROBE = r"""
import importlib.abc, json, math, socket, sys

forbidden = (
    "src.config", "src.llm", "src.agent", "src.scheduler", "src.notification",
    "src.report_language", "data_provider", "api", "litellm", "openai",
    "google.genai", "google.generativeai",
)
class BlockProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("data configuration imported a product or SDK: " + fullname)

sys.meta_path.insert(0, BlockProducts())
def deny(self, address):
    raise AssertionError("data configuration attempted a network connection")
socket.socket.connect = deny
from src.data_config import get_data_config

config = get_data_config()
def encode(value):
    if isinstance(value, float) and not math.isfinite(value):
        if math.isnan(value):
            name = "nan"
        elif value > 0:
            name = "inf"
        else:
            name = "-inf"
        return {"__float__": name}
    return value
fields = json.loads(sys.argv[1])
print("__DATA_CONFIG_RESULT__" + json.dumps(
    {"values": {name: encode(getattr(config, name)) for name in fields},
     "cached": get_data_config() is config,
     "forbidden": [name for name in sys.modules
                   if any(name == p or name.startswith(p + ".") for p in forbidden)]},
    ensure_ascii=False,
))
"""


@pytest.mark.parametrize("case", FIXTURE["scenarios"], ids=lambda case: case["name"])
def test_dotenv_and_cache_load_without_products_sdks_or_network(case, tmp_path):
    env_file = tmp_path / "data.env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in case["dotenv"].items()))
    result = subprocess.run(
        [sys.executable, "-c", ISOLATED_PROBE, json.dumps(FIXTURE["fields"])],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": os.environ.get("PATH", ""),
            "LANG": "en_US.UTF-8",
            "ENV_FILE": str(env_file),
            **case["environment"],
        },
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr[-1200:]
    payload = json.loads(result.stdout.split("__DATA_CONFIG_RESULT__")[-1])
    assert payload["values"] == case["expected"]
    assert payload["cached"] is True
    assert payload["forbidden"] == []


def test_data_contract_has_only_frozen_data_fields():
    assert sorted(DataServiceConfig.__dataclass_fields__) == FIXTURE["fields"]


def test_credentials_and_private_urls_are_not_rendered_or_logged(caplog):
    marker = "controlled-sensitive-canary"
    config = DataServiceConfig.from_env(
        {
            "TUSHARE_TOKEN": marker,
            "TICKFLOW_API_KEY": marker,
            "LONGBRIDGE_APP_SECRET": marker,
            "LONGBRIDGE_ACCESS_TOKEN": marker,
            "ALPHAVANTAGE_API_KEY": marker,
            "FINNHUB_API_KEY": marker,
            "TAVILY_API_KEYS": marker,
            "BOCHA_API_KEYS": marker,
            "ANSPIRE_API_KEYS": marker,
            "MINIMAX_API_KEYS": marker,
            "BRAVE_API_KEYS": marker,
            "SERPAPI_API_KEYS": marker,
            "SEARXNG_BASE_URLS": marker,
        }
    )
    assert marker not in repr(config)
    assert marker not in caplog.text
    assert config.tushare_token == marker
    assert config.searxng_base_urls == []


def test_proxy_environment_preserves_existing_bypass_and_alias_semantics():
    values = {
        "HTTP_PROXY": "http://controlled.invalid:8123",
        "https_proxy": "http://controlled.invalid:8124",
        "NO_PROXY": "private.invalid",
    }
    configure_data_proxy_environment(values)
    assert values["http_proxy"] == values["HTTP_PROXY"]
    assert values["HTTPS_PROXY"] == values["https_proxy"]
    assert values["NO_PROXY"] == values["no_proxy"]
    assert {"private.invalid", "cninfo.com.cn", "localhost", "127.0.0.1"} <= set(
        values["NO_PROXY"].split(",")
    )
    untouched = {"NO_PROXY": "private.invalid"}
    configure_data_proxy_environment(untouched)
    assert untouched == {"NO_PROXY": "private.invalid"}


def test_loading_failure_is_distinct_and_does_not_cache_missing_credentials(tmp_path, monkeypatch):
    with monkeypatch.context() as isolated:
        isolated.setattr(os, "environ", {"ENV_FILE": str(tmp_path)})
        reset_data_config()
        try:
            with pytest.raises(DataConfigurationError, match="不是文件"):
                get_data_config()
            env_file = tmp_path / "recovered.env"
            env_file.write_text("TUSHARE_TOKEN=fixture-recovered")
            os.environ["ENV_FILE"] = str(env_file)
            assert get_data_config().tushare_token == "fixture-recovered"
        finally:
            reset_data_config()


def test_missing_optional_file_keeps_real_missing_credentials(tmp_path, monkeypatch):
    with monkeypatch.context() as isolated:
        isolated.setattr(os, "environ", {"ENV_FILE": str(tmp_path / "not-created.env")})
        reset_data_config()
        try:
            assert get_data_config().tushare_token is None
        finally:
            reset_data_config()


def test_unreadable_configuration_is_not_an_empty_credential_result(tmp_path, monkeypatch):
    env_file = tmp_path / "bad-encoding.env"
    env_file.write_bytes(bytes([255]))
    with monkeypatch.context() as isolated:
        isolated.setattr(os, "environ", {"ENV_FILE": str(env_file)})
        reset_data_config()
        try:
            with pytest.raises(DataConfigurationError, match="文件加载失败"):
                get_data_config()
        finally:
            reset_data_config()
