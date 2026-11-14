"""临时真实SQLite中的逐次失败、升级和分页；不读取外部文档。"""

import sqlite3
from uuid import uuid4

from src.services.rule_monitor_history import RuleMonitorHistory
from src.services.rule_monitor_observations import RuleMonitorObservations
from src.services.rule_monitor_store import RuleMonitorStore

SOURCE = "official-rules/cn-stock-auction-fees"


def test_history_freezes_failures_and_trigger_across_reopen_and_later_success(tmp_path):
    now = [1000.0]
    path = tmp_path / "monitor.sqlite"
    store = RuleMonitorStore(path, clock=lambda: now[0])
    first, second = str(uuid4()), str(uuid4())
    store.enqueue(first, [SOURCE]); store.claim("owner")
    observations = RuleMonitorObservations(store)
    observations.record(first, "owner", kind="document", identity="https://www.sse.com.cn/rule",
        name="首次资料", error="document_http_error")
    store.finish(first, "owner")
    now[0] += 86401
    store.configure(0, True, [SOURCE])
    now[0] += 86400
    assert store.schedule_due(second)
    store.claim("owner")
    observations.record(second, "owner", kind="document", identity="https://www.sse.com.cn/rule",
        name="当前资料", digest="a" * 64, raw_digest="b" * 64)
    store.finish(second, "owner")
    reader = RuleMonitorHistory(RuleMonitorStore(path))
    latest = reader.read(limit=1)
    assert latest["total"] == 2
    assert latest["items"][0]["trigger"] == "scheduled"
    assert latest["items"][0]["failures"] == []
    previous = reader.read(offset=1, limit=1)["items"][0]
    assert previous["trigger"] == "manual"
    assert previous["failures"][0]["name"] == "首次资料"
    assert previous["failures"][0]["errorCode"] == "document_http_error"
    issues = reader.read(issues_only=True)
    assert issues["total"] == 1 and issues["items"][0]["id"] == first
    assert reader.read(offset=10)["items"] == []


def test_v1_upgrade_preserves_existing_rows_and_marks_unknown_history(tmp_path):
    path = tmp_path / "old.sqlite"
    store = RuleMonitorStore(path)
    identity = str(uuid4())
    store.configure(0, False, [SOURCE])
    store.enqueue(identity, [SOURCE]); store.claim("owner")
    observations = RuleMonitorObservations(store)
    observations.record(identity, "owner", kind="document", identity="https://www.sse.com.cn/rule",
        name="历史资料", digest="a" * 64, raw_digest="b" * 64)
    store.finish(identity, "owner", "monitor_execution_failed")
    before = store.job(identity)
    previous_config, previous_observations = store.config(), observations.status()
    previous_versions = observations.versions("document", "https://www.sse.com.cn/rule")
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE run_metadata")
        db.execute("DROP TABLE run_failures")
        db.execute("DROP TABLE schedule")
        db.execute("PRAGMA user_version=1")
    upgraded = RuleMonitorStore(path)
    assert upgraded.job(identity) == before
    assert upgraded.config() == previous_config
    assert RuleMonitorObservations(upgraded).status() == previous_observations
    assert RuleMonitorObservations(upgraded).versions("document", "https://www.sse.com.cn/rule") == previous_versions
    entry = RuleMonitorHistory(upgraded).read()["items"][0]
    assert entry["trigger"] == "unknown"
    assert entry["failureDetailsAvailable"] is False
    with upgraded.connection() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 4


def test_expired_attempt_cannot_append_failure_history(tmp_path):
    now = [1000.0]
    store = RuleMonitorStore(tmp_path / "monitor.sqlite", clock=lambda: now[0])
    identity = str(uuid4())
    store.enqueue(identity, [SOURCE]); store.claim("owner")
    now[0] += 361
    store.claim("new-owner")
    assert not RuleMonitorObservations(store).record(identity, "owner", kind="document",
        identity="https://www.sse.com.cn/rule", name="晚到结果", error="document_unavailable")
    history = RuleMonitorHistory(store).read(issues_only=True)
    assert history["items"][0]["status"] == "interrupted"
    assert history["items"][0]["failures"] == []
