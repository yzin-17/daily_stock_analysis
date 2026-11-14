"""监测任务的持久状态、并发领取、版本安静与失败保留。"""

from uuid import uuid4

import pytest

from src.services.rule_monitor_store import MonitorConflict, RuleMonitorStore
from src.services.rule_monitor_observations import RuleMonitorObservations

SOURCE = "official-rules/cn-stock-auction-fees"


def test_config_cas_disabled_scheduler_and_expired_attempt_cannot_commit(tmp_path):
    now = [1000.0]
    store = RuleMonitorStore(tmp_path / "monitor.sqlite", clock=lambda: now[0])
    assert store.config() == {"revision": 0, "enabled": False, "sourceIds": [],
        "taskCreated": False, "cron": "@every24h", "timezone": "Asia/Shanghai", "nextRunAt": None}
    assert store.schedule_due(str(uuid4())) is False
    store.configure(0, True, [SOURCE])
    with pytest.raises(MonitorConflict):
        store.configure(0, False, [])
    assert store.schedule_due(str(uuid4())) is False
    now[0] += 86400
    assert store.schedule_due(str(uuid4())) is True
    assert store.schedule_due(str(uuid4())) is False
    job = store.claim("worker-a")
    assert job["attempt"] == 1
    assert store.claim("worker-b") is None
    now[0] += 361
    assert store.claim("worker-b") is None
    assert store.job(job["id"])["status"] == "interrupted"
    assert not store.finish(job["id"], "worker-a")
    assert not RuleMonitorObservations(store).record(job["id"], "worker-a", kind="pack", identity=SOURCE,
        name="旧执行", digest="a" * 64)
    now[0] += 86400
    assert store.schedule_due(str(uuid4()))
    store.configure(1, False, [SOURCE])
    assert not store.schedule_due(str(uuid4()))


def test_idempotent_manual_job_and_busy_boundary(tmp_path):
    store = RuleMonitorStore(tmp_path / "monitor.sqlite")
    identity = str(uuid4())
    first = store.enqueue(identity, [SOURCE])
    assert store.enqueue(identity, [SOURCE]) == first
    with pytest.raises(MonitorConflict, match="request_identity_conflict"):
        store.enqueue(identity, ["other/source"])
    with pytest.raises(MonitorConflict, match="monitor_busy"):
        store.enqueue(str(uuid4()), [SOURCE])
    assert store.job(str(uuid4())) is None


def test_unchanged_is_not_new_version_and_failure_preserves_last_good_value(tmp_path):
    now = [1000.0]
    path = tmp_path / "monitor.sqlite"
    store = RuleMonitorStore(path, clock=lambda: now[0])
    observations = RuleMonitorObservations(store)
    for digest, error in [("a" * 64, None), ("a" * 64, None), ("b" * 64, None), (None, "document_unavailable")]:
        now[0] += 1
        identity = str(uuid4())
        store.enqueue(identity, [SOURCE]); store.claim("owner")
        assert observations.record(identity, "owner", kind="document", identity="https://www.sse.com.cn/rule",
            name="公开规则", digest=digest, raw_digest=digest, error=error)
        assert store.finish(identity, "owner")
        if digest == "a" * 64:
            assert observations.status()[0]["versionCount"] == 1
    current = observations.status()[0]
    assert current["contentHash"] == "b" * 64
    assert current["status"] == "unavailable"
    assert current["lastChangeAt"] is not None
    assert current["lastSuccessAt"] != current["lastCheckedAt"]
    assert current["versionCount"] == 2
    assert store.job()["status"] == "partial"
    reopened = RuleMonitorObservations(RuleMonitorStore(path))
    assert reopened.status() == observations.status()
    assert len(reopened.versions("document", current["identity"])) == 2
