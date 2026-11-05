"""监测恢复、认证入口与有限执行；全部外部文档使用受控读取。"""

from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_rule_monitor import router
from src.services.rule_monitor_registry import Source
from src.services.rule_monitor_runtime import RuleMonitorRuntime, VERSION
from src.services.rule_monitor_store import RuleMonitorStore

IDENTITY = "official-rules/cn-stock-auction-fees"
URL = "https://www.sse.com.cn/controlled-rule"


def setup(tmp_path):
    calls = []
    async def fetch(url, **kwargs):
        calls.append(url)
        assert kwargs["budget_seconds"] <= 300
        return {"contentHash": "b" * 64, "rawHash": "c" * 64}
    source = Source(IDENTITY, "CN", "受控规则", "controlled-v1", "a" * 64,
        ({"id": "first", "title": "公开资料", "url": URL}, {"id": "again", "title": "公开资料", "url": URL}))
    runtime = RuleMonitorRuntime(RuleMonitorStore(tmp_path / "monitor.sqlite"), registry=lambda: {IDENTITY: source}, fetch=fetch)
    return runtime, calls


def test_persisted_queued_job_runs_once_deduplicates_documents_and_reopen_is_quiet(tmp_path):
    monitor, calls = setup(tmp_path)
    identity = str(uuid4())
    monitor.enqueue(identity, [IDENTITY])
    assert monitor.store.job(identity)["status"] == "queued"
    assert monitor.run_pending_once()
    assert monitor.store.job(identity)["status"] == "succeeded"
    assert monitor.store.job(identity)["counts"] == {"documents": 1, "changed": 0, "unavailable": 0}
    assert not monitor.run_pending_once()
    reopened, _ = setup(tmp_path)
    assert not reopened.run_pending_once()
    assert len(calls) == 1
    monitor.enqueue(str(uuid4()), [IDENTITY]); monitor.run_pending_once()
    assert all(item["versionCount"] == 1 and item["status"] == "unchanged" for item in monitor.status()["observations"])


def test_control_auth_strict_input_cas_idempotence_and_missing_job(tmp_path, monkeypatch):
    monitor, calls = setup(tmp_path)
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "monitor-control")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "monitor-data")
    app = FastAPI(); app.state.rule_monitor = monitor; app.include_router(router, prefix="/api/v3")
    headers = {"Authorization": "Bearer monitor-control"}
    root = "/api/v3/thesis-ledger/control/rule-monitor"
    with TestClient(app) as client:
        assert client.get(root).status_code == 401
        assert client.get(root, headers={"Authorization": "Bearer monitor-data"}).status_code == 401
        assert client.get(root, headers=headers).json()["config"]["enabled"] is False
        request = {"version": VERSION, "requestId": str(uuid4()), "sourceIds": [IDENTITY]}
        assert client.post(root + "/checks", json={**request, "url": URL}, headers=headers).status_code == 422
        assert client.post(root + "/checks", json={**request, "sourceIds": ["unknown/source"]}, headers=headers).status_code == 422
        assert not calls and monitor.store.job() is None
        configuration = {"version": VERSION, "expectedRevision": 0, "enabled": False, "sourceIds": [IDENTITY]}
        assert client.put(root + "/config", json=configuration, headers=headers).status_code == 200
        assert client.put(root + "/config", json=configuration, headers=headers).status_code == 409
        created = client.post(root + "/checks", json=request, headers=headers)
        assert created.status_code == 200
        assert client.post(root + "/checks", json=request, headers=headers).json() == created.json()
        assert monitor.run_pending_once()
        read = client.get(root + "/checks/" + request["requestId"], headers=headers)
        assert read.json()["run"]["status"] == "succeeded"
        assert client.get(root + "/checks/" + str(uuid4()), headers=headers).status_code == 404
        versions = client.get(root + "/versions", params={"kind": "document", "identity": URL}, headers=headers)
        assert len(versions.json()["versions"]) == 1
