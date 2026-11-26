"""来源按登记目录扩展；全部读写使用隔离 SQLite 和受控文档。"""

from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_rule_monitor import router
from src.services.rule_monitor_runtime import VERSION
from tests.test_rule_monitor_runtime import setup, IDENTITY


@pytest.mark.parametrize("count", [10, 17])
def test_catalog_growth_round_trips_all_routes_and_keeps_admission_guards(tmp_path, monkeypatch, count):
    monitor, calls = setup(tmp_path)
    original = monitor.registry()[IDENTITY]
    catalog = {f"official-rules/source-{index}": replace(original, identity=f"official-rules/source-{index}")
               for index in range(count)}
    monitor.registry = Mock(return_value=catalog)
    identities = list(catalog)
    monitor.validate_sources(identities)
    monitor.registry.assert_called_once_with()
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "monitor-control")
    app = FastAPI()
    app.state.rule_monitor = monitor
    app.include_router(router, prefix="/api/v1")
    root = "/api/v1/thesis-ledger/control/rule-monitor"
    headers = {"Authorization": "Bearer monitor-control"}
    with TestClient(app) as client:
        previous = monitor.store.config()
        for invalid in [[], identities + [identities[0]], identities + ["unknown/source"], ["bad"]]:
            task = {"version": VERSION, "expectedRevision": 0, "sourceIds": invalid}
            assert client.post(root + "/task", json=task, headers=headers).status_code == 422
            assert client.put(root + "/config", json={**task, "enabled": True}, headers=headers).status_code == 422
            check = {"version": VERSION, "requestId": str(uuid4()), "sourceIds": invalid}
            assert client.post(root + "/checks", json=check, headers=headers).status_code == 422
            assert monitor.store.config() == previous
            assert monitor.store.job() is None and calls == []
        body = {"version": VERSION, "expectedRevision": 0, "sourceIds": identities}
        created = client.post(root + "/task", json=body, headers=headers)
        assert created.status_code == 200
        config = created.json()["config"]
        assert config["sourceIds"] == identities and config["revision"] == 1
        assert client.post(root + "/task", json=body, headers=headers).status_code == 409
        configured = client.put(root + "/config", json={**body, "expectedRevision": 1, "enabled": False}, headers=headers)
        assert configured.status_code == 200
        assert configured.json()["config"]["sourceIds"] == identities
        updated = client.post(root + "/task", json={**body, "expectedRevision": 2}, headers=headers)
        assert updated.status_code == 200
        assert updated.json()["config"]["enabled"] is False
        assert updated.json()["config"]["nextRunAt"] == configured.json()["config"]["nextRunAt"]
        request_id = str(uuid4())
        queued = client.post(root + "/checks", json={"version": VERSION, "requestId": request_id,
                             "sourceIds": identities}, headers=headers)
        assert queued.status_code == 200
        assert queued.json()["run"]["sourceIds"] == identities
        assert monitor.run_pending_once()
        assert monitor.store.job(request_id)["status"] == "succeeded"
        assert len(calls) == 1
        status = client.get(root, headers=headers).json()
        assert [source["identity"] for source in status["sources"]] == identities
        assert status["config"]["sourceIds"] == status["latestRun"]["sourceIds"] == identities
        assert client.get(root + "/history", headers=headers).json()["items"][0]["sourceIds"] == identities
