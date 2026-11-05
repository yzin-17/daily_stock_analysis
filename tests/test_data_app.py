"""专用数据应用的认证、协议退役、错误及无产品导入启动。"""

from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient as ApiClient
import pytest

from api.data_app import create_app
from src.data_config import DataConfigurationError


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "fixture-data-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "fixture-control-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data.db"))
    return ApiClient(create_app(), raise_server_exceptions=False)


def test_health_and_root_are_data_json_without_frontend(client):
    for path in ("/health", "/api/health"):
        response = client.get(path)
        assert response.status_code == 200 and response.json()["status"] == "ok"
        datetime.fromisoformat(response.json()["timestamp"])
    assert client.get("/").json() == {"service": "dsa-data", "status": "ok"}
    assert client.get("/assets/fixture.js").status_code == 404
    assert client.get("/history").status_code == 404


@pytest.mark.parametrize("path", ["/api/v1/analysis", "/api/v1/agent/chat", "/api/v1/portfolio",
                                  "/api/v1/thesis-ledger", "/api/v2/thesis-ledger/market/quote"])
@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
def test_retired_prefixes_return_same_not_found_without_spa_or_product_handlers(client, path, method):
    response = client.request(method, path)
    assert response.status_code == 404 and response.json() == {"error": "not_found"}
    assert "text/html" not in response.headers["content-type"]


def test_data_and_control_tokens_remain_separate_and_request_error_is_structured(client):
    path = "/api/v3/thesis-ledger/market/fx-rates"
    params = {"baseCurrency": "CNY", "currencies": "USD", "asOf": "2026-10-16"}
    assert client.get(path, params=params).status_code == 401
    assert client.get(path, params=params, headers={"authorization": "Bearer fixture-control-token"}).status_code == 401
    headers = {"authorization": "Bearer fixture-data-token", "x-request-id": "data-app-fixture-request"}
    assert client.get(path, params=params, headers=headers).json()["version"] == 3
    bad = client.get(path, params={**params, "asOf": "bad"}, headers=headers)
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "invalid_as_of"
    assert bad.json()["detail"]["requestId"] == "data-app-fixture-request"
    control = "/api/v3/thesis-ledger/control/providers"
    assert client.get(control, headers=headers).status_code == 401
    assert client.get(control, headers={"authorization": "Bearer fixture-control-token"}).status_code == 200


@pytest.mark.parametrize("error,status", [(DataConfigurationError("fixture-private-canary"), 503),
                                          (RuntimeError("fixture-private-canary"), 500)])
def test_internal_failures_do_not_render_or_log_private_values(monkeypatch, error, status, caplog):
    app = create_app()

    @app.get("/fixture-error")
    def fail():
        raise error

    response = ApiClient(app, raise_server_exceptions=False).get("/fixture-error")
    assert response.status_code == status
    assert "fixture-private-canary" not in response.text
    assert "fixture-private-canary" not in caplog.text


PROBE = r"""
import asyncio, importlib.abc, socket, sys
forbidden = ("api.app", "api.v1", "src.config", "src.auth", "src.storage", "src.agent", "src.llm",
             "src.notification", "src.report_language", "src.stock_analyzer", "src.scheduler",
             "src.services.system_config_service", "src.services.runtime_scheduler", "litellm", "openai")
class BlockProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("data application imported " + fullname)
sys.meta_path.insert(0, BlockProducts())
def deny(self, address):
    raise AssertionError("data application attempted external connection")
socket.socket.connect = deny
from server import app
import httpx
async def run():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            assert (await client.get("/api/health")).json()["status"] == "ok"
            schema = (await client.get("/openapi.json")).json()
            assert all(path.startswith("/api/v3/thesis-ledger/") or path in ("/api/health", "/health")
                       for path in schema["paths"])
            assert "/api/v3/thesis-ledger/control/providers/longbridge/oauth/sessions" in schema["paths"]
            assert (await client.post("/api/v1/analysis")).status_code == 404
            assert (await client.get("/stocks.index.json")).status_code == 200
    assert not any(name == p or name.startswith(p + ".") for name in sys.modules for p in forbidden)
asyncio.run(run())
print("data-app-startup-ok")
"""


def test_real_server_import_startup_and_http_without_product_modules_or_frontend(tmp_path):
    env_file = tmp_path / "data.env"
    env_file.write_text("")
    result = subprocess.run(
        [sys.executable, "-c", PROBE], cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "ENV_FILE": str(env_file),
             "DATABASE_PATH": str(tmp_path / "data.db"), "LOG_DIR": str(tmp_path / "logs"),
             "STOCK_INDEX_REMOTE_UPDATE_ENABLED": "false"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr[-1800:]
    assert "data-app-startup-ok" in result.stdout
