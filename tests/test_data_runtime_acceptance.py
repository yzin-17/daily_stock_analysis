"""物理无模型 SDK 的数据进程冷启动、HTTP 路由与重复关闭验收。"""

from contextlib import contextmanager
import importlib.metadata
import json
import os
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import tempfile
from threading import Thread
import time

import pytest
import requests

from tests.data_runtime_environment import build_data_runtime_environment

REPOSITORY = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def data_python():
    supplied = os.environ.get("DSA_DATA_RUNTIME_PYTHON")
    if supplied:
        executable = Path(supplied).resolve()
        assert executable.is_file()
        yield supplied
        return
    root = Path(tempfile.mkdtemp(prefix="dsa-data-runtime-"))
    try:
        report = build_data_runtime_environment(root)
        assert all(importlib.metadata.version(name) == version for name, version in report["packages"].items())
        yield report["python"]
    finally:
        shutil.rmtree(root)


@contextmanager
def running_data_process(executable, root, *, scenario=None):
    env_file = root / "empty.env"
    env_file.write_text("")
    environment = {
        "PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "PYTHONPATH": str(REPOSITORY),
        "PYTHONNOUSERSITE": "1", "ENV_FILE": str(env_file), "DATABASE_PATH": str(root / "data.db"),
        "LOG_DIR": str(root / "logs"), "STOCK_INDEX_REMOTE_UPDATE_ENABLED": "false",
        "THESIS_LEDGER_DSA_TOKEN": "data-runtime-controlled-token",
        "THESIS_LEDGER_CONTROL_TOKEN": "data-runtime-controlled-control",
        "THESIS_LEDGER_CONTROL_ENCRYPTION_KEY": "data-runtime-controlled-encryption",
        "THESIS_LEDGER_FIXTURE_MODE": "false",
    }
    if scenario:
        environment["DSA_DATA_RUNTIME_SCENARIO"] = scenario
    process = subprocess.Popen([executable, "-m", "tests.data_runtime_process"], cwd=REPOSITORY,
                               env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
    messages = Queue()
    output = []

    def consume():
        for line in process.stdout:
            output.append(line)
            if line.startswith("DSA_RUNTIME:"):
                messages.put(json.loads(line[len("DSA_RUNTIME:"):]))

    reader = Thread(target=consume, daemon=True)
    reader.start()
    session = requests.Session()
    session.trust_env = False
    bound = None
    try:
        try:
            bound = messages.get(timeout=15)
        except Empty:
            pytest.fail("数据进程未监听：" + "".join(output)[-2400:])
        assert bound["kind"] == "bound", bound
        assert not any(bound["models"].values()) and len(bound["sdks"]) == 12
        origin = f"http://127.0.0.1:{bound['port']}"
        deadline = time.monotonic() + 10
        while True:
            try:
                if session.get(origin + "/health", timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                pass
            assert process.poll() is None and time.monotonic() < deadline, "".join(output)[-2400:]
            time.sleep(0.05)
        session.data_cases = bound["cases"]
        yield origin, session
    finally:
        session.close()
        if process.poll() is None:
            process.stdin.write("stop\n")
            process.stdin.flush()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
            pytest.fail("数据进程关闭超时：" + "".join(output)[-2400:])
        reader.join(2)
        process.stdin.close()
        process.stdout.close()
        assert process.returncode == 0, "".join(output)[-2400:]
        if bound is not None:
            assert messages.get(timeout=1) == {
                "kind": "stopped", "forbiddenImports": [], "children": 0,
                "lifecycleActive": False, "oauthActive": False,
            }


def test_physical_data_environment_server_entrypoint_and_restart(data_python, tmp_path):
    for _ in range(2):
        with running_data_process(data_python, tmp_path) as (origin, session):
            assert session.get(origin + "/").json() == {"service": "dsa-data", "status": "ok"}
            schema = session.get(origin + "/openapi.json", timeout=5).json()
            paths = set(schema["paths"])
            required = {
                "/market/quote", "/market/bars", "/market/chart-bars", "/market/events",
                "/market/indicators/calculate", "/market/fx-rates", "/market/fund-nav",
                "/market/fund-nav/history", "/market/fund-holdings", "/market/calendar",
                "/backtest/calendar", "/backtest/instrument-facts", "/backtest/nav-inputs",
                "/market/security-lot-size", "/market/security-static-info", "/market/status-capabilities",
                "/market/announcements", "/market/news", "/market/chip", "/market/cache-context",
                "/control/providers", "/control/policies/effective", "/control/catalog/jobs",
                "/catalog/snapshot", "/catalog/delta",
                "/control/providers/longbridge/oauth/sessions",
            }
            assert {"/api/v3/thesis-ledger" + path for path in required} <= paths
            assert all(path.startswith("/api/v3/thesis-ledger/") or path in ("/health", "/api/health")
                       for path in paths)
            for path in paths - {"/health", "/api/health"}:
                route = path.replace("{provider_id}", "longbridge").replace("{job_id}", "missing")
                route = route.replace("{session_id}", "missing")
                method = next(method for method in schema["paths"][path] if method in {"get", "post"})
                response = session.request(method, origin + route, json={} if method == "post" else None, timeout=5)
                expected = 200 if path == "/api/v3/thesis-ledger/capabilities" else 401
                assert response.status_code == expected, (path, response.status_code, response.text[:200])
            index = session.get(origin + "/stocks.index.json", timeout=5)
            assert index.status_code == 200 and len(index.json()) > 1000
            for prefix in ("v1", "v2"):
                for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
                    response = session.request(method, origin + f"/api/{prefix}/analysis", timeout=3)
                    assert response.status_code == 404
                    if method != "HEAD":
                        assert response.json() == {"error": "not_found"}


@pytest.fixture(scope="module")
def matrix_python():
    supplied = os.environ.get("DSA_DATA_RUNTIME_PYTHON")
    if supplied:
        yield supplied
        return
    root = Path(tempfile.mkdtemp(prefix="dsa-data-runtime-"))
    try:
        yield build_data_runtime_environment(root, with_tests=True)["python"]
    finally:
        shutil.rmtree(root)


@pytest.mark.parametrize("scenario", ["basics", "bars", "events", "nav", "rules", "reference"])
def test_independent_http_categories_from_native_inputs(matrix_python, tmp_path, scenario):
    with running_data_process(matrix_python, tmp_path, scenario=scenario) as (origin, session):
        for case in session.data_cases:
            response = session.request(case["method"], origin + case["path"],
                                       json=case.get("body"), params=case.get("query"),
                                       headers={"Authorization": "Bearer data-runtime-controlled-token"}, timeout=10)
            assert response.status_code == case["status"], (case["client"], response.status_code, response.text[:500])
            result = response.json()
            if case["status"] == 200:
                assert isinstance(result, dict) and ("contractVersion" in result or "version" in result)
                if case["client"] == "marketEventsV3":
                    assert result["coverage"]["complete"] is False and result["facts"] == []
                if case["client"] == "marketBarsV3":
                    assert len(result["bars"]) > 20 and result["sourcePriceBasis"]["adjustment"] == "qfq"
                if case["client"] == "marketNavInputsV3":
                    assert result["facts"] and result["navVisibility"]["mode"] == "research-assumption"
