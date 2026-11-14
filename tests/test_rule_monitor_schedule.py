"""持久计划、增量升级与受控执行闭环；不访问外部资料。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_rule_monitor import router
from src.services.rule_monitor_runtime import VERSION
from src.services.rule_monitor_schedule import next_occurrence
from src.services.rule_monitor_store import MonitorConflict, RuleMonitorStore
from tests.test_rule_monitor_runtime import setup, IDENTITY


def epoch(value):
    return datetime.fromisoformat(value).timestamp()


def test_create_default_24_hours_update_sources_and_manual_check_preserve_due(tmp_path):
    now = [epoch("2026-11-09T10:00:00+00:00")]
    store = RuleMonitorStore(tmp_path / "plan.sqlite", clock=lambda: now[0])
    config = store.create_task(0, [IDENTITY])
    assert config["taskCreated"] and config["enabled"]
    assert epoch(config["nextRunAt"]) == now[0] + 86400
    due = config["nextRunAt"]
    store.enqueue(str(uuid4()), [IDENTITY]); job = store.claim("owner"); store.finish(job["id"], "owner")
    now[0] += 3600
    assert store.create_task(1, [IDENTITY])["nextRunAt"] == due
    with pytest.raises(MonitorConflict): store.create_task(1, [IDENTITY])
    assert not store.schedule_due(str(uuid4()))
    reopened = RuleMonitorStore(store.path, clock=lambda: now[0])
    assert reopened.config() == store.config()
    now[0] = epoch(due)
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda _: reopened.schedule_due(str(uuid4())), range(2)))
    assert claimed.count(True) == 1
    assert epoch(reopened.config()["nextRunAt"]) == now[0] + 86400


@pytest.mark.parametrize("expression,zone,after,expected", [
    ("0 9 * * *", "Asia/Shanghai", "2026-11-09T10:00:00+00:00", "2026-11-10T01:00:00+00:00"),
    ("0 9 * * 1-5", "Asia/Shanghai", "2026-11-06T02:00:00+00:00", "2026-11-09T01:00:00+00:00"),
    ("*/15 * * * *", "UTC", "2026-11-09T10:00:00+00:00", "2026-11-09T10:15:00+00:00"),
    ("0 9 * * *", "America/New_York", "2026-10-31T14:00:00+00:00", "2026-11-01T14:00:00+00:00"),
    ("@every24h", "America/New_York", "2026-10-31T14:00:00+00:00", "2026-11-01T14:00:00+00:00"),
])
def test_schedule_presets_custom_and_timezone(expression, zone, after, expected):
    assert next_occurrence(expression, zone, epoch(after)) == epoch(expected)


@pytest.mark.parametrize("expression,zone", [("0 0 31 2 *", "UTC"), ("bad cron", "UTC"),
    ("* * * * * *", "UTC"), ("0 9 * * *", "Invalid/Zone")])
def test_invalid_schedule_is_rejected_without_changing_config(tmp_path, expression, zone):
    store = RuleMonitorStore(tmp_path / "invalid.sqlite")
    previous = store.create_task(0, [IDENTITY])
    with pytest.raises((ValueError, KeyError)):
        store.set_schedule(1, expression, zone)
    assert store.config() == previous


def test_disable_resume_plan_change_and_bounded_catchup(tmp_path):
    now = [epoch("2026-11-09T10:00:00+00:00")]
    store = RuleMonitorStore(tmp_path / "state.sqlite", clock=lambda: now[0])
    store.create_task(0, [IDENTITY])
    config = store.set_schedule(1, "0 9 * * *", "Asia/Shanghai")
    assert epoch(config["nextRunAt"]) == epoch("2026-11-10T01:00:00+00:00")
    store.configure(2, False, [IDENTITY])
    now[0] += 10 * 86400
    assert not store.schedule_due(str(uuid4()))
    enabled = store.configure(3, True, [IDENTITY])
    assert epoch(enabled["nextRunAt"]) > now[0]
    now[0] += 10 * 86400
    assert store.schedule_due(str(uuid4()))
    assert not store.schedule_due(str(uuid4()))
    job = store.claim("owner"); store.finish(job["id"], "owner")
    assert not store.schedule_due(str(uuid4()))
    with pytest.raises(MonitorConflict): store.set_schedule(3, "0 8 * * *", "UTC")


def test_config_and_schedule_are_one_read_snapshot_during_updates(tmp_path):
    store = RuleMonitorStore(tmp_path / "concurrent.sqlite")
    store.create_task(0, [IDENTITY])
    def writer():
        for index in range(20):
            cron = "0 9 * * *" if index % 2 == 0 else "0 10 * * *"
            store.set_schedule(index + 1, cron, "UTC")
    def reader():
        for _ in range(100):
            config = store.config()
            if config["revision"] == 1:
                assert config["cron"] == "@every24h"
            else:
                expected = "0 9 * * *" if config["revision"] % 2 == 0 else "0 10 * * *"
                assert config["cron"] == expected
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(writer), pool.submit(reader)]
        for future in futures: future.result()


def test_v2_upgrade_preserves_existing_config_history_and_versions(tmp_path):
    now = [epoch("2026-11-09T10:00:00+00:00")]
    path = tmp_path / "v2.sqlite"
    store = RuleMonitorStore(path, clock=lambda: now[0])
    store.configure(0, True, [IDENTITY])
    monitor, _ = setup(tmp_path / "controlled")
    monitor.store = store
    from src.services.rule_monitor_observations import RuleMonitorObservations
    monitor.observations = RuleMonitorObservations(store)
    monitor.enqueue(str(uuid4()), [IDENTITY]); monitor.run_pending_once()
    observations, run = monitor.observations.status(), store.job()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE schedule"); db.execute("PRAGMA user_version=2")
    upgraded = RuleMonitorStore(path, clock=lambda: now[0])
    assert upgraded.config()["revision"] == 1 and upgraded.config()["enabled"]
    assert upgraded.config()["cron"] == "@every24h"
    assert epoch(upgraded.config()["nextRunAt"]) == now[0] + 86400
    assert upgraded.job() == run
    assert RuleMonitorObservations(upgraded).status() == observations


def test_authenticated_creation_schedule_and_due_execution_without_remote_calls(tmp_path, monkeypatch):
    monitor, calls = setup(tmp_path)
    now = [epoch("2026-11-09T10:00:00+00:00")]
    monitor.store.clock = lambda: now[0]
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "schedule-control")
    app = FastAPI(); app.state.rule_monitor = monitor; app.include_router(router, prefix="/api/v3")
    root = "/api/v3/thesis-ledger/control/rule-monitor"
    headers = {"Authorization": "Bearer schedule-control"}
    body = {"version": VERSION, "expectedRevision": 0, "sourceIds": [IDENTITY]}
    with TestClient(app) as client:
        assert client.post(root + "/task", json=body).status_code == 401
        assert client.post(root + "/task", json={**body, "cron": "* * * * *"}, headers=headers).status_code == 422
        created = client.post(root + "/task", json=body, headers=headers)
        assert created.status_code == 200 and created.json()["config"]["cron"] == "@every24h"
        schedule = {"version": VERSION, "expectedRevision": 1, "cron": "*/15 * * * *", "timezone": "UTC"}
        assert client.put(root + "/schedule", json=schedule).status_code == 401
        assert client.put(root + "/schedule", json={**schedule, "sourceIds": [IDENTITY]}, headers=headers).status_code == 422
        assert client.put(root + "/schedule", json=schedule, headers=headers).status_code == 200
        assert client.put(root + "/schedule", json=schedule, headers=headers).status_code == 409
        assert not calls and monitor.store.job() is None
        now[0] += 900
        assert monitor.store.schedule_due(str(uuid4()))
        assert monitor.run_pending_once()
        assert monitor.store.job()["status"] == "succeeded" and len(calls) == 1
        assert not monitor.run_pending_once()
