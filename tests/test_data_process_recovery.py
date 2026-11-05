"""真实进程中断后，公告、目录与 OAuth 在保留副本中有界收敛。"""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import sqlite3
import subprocess
import sys
from threading import Event
import time


def fingerprint_rows(path):
    result = {}
    with closing(sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)) as connection:
        for (table,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            name = '"' + table.replace('"', '""') + '"'
            cols = [r[1] for r in connection.execute("PRAGMA table_info(" + name + ")")]
            original = connection.execute("SELECT * FROM " + name).fetchall()
            rows = [hashlib.sha256(repr(tuple(row)).encode()).hexdigest() for row in original]
            generations = {}
            if table == "thesis_ledger_catalog_generation":
                generations = {int(row[cols.index("generation")]): sha for row, sha in zip(original, rows)}
            result[table] = (cols, rows, generations)
    return result


def prepare(tmp_path):
    root = tmp_path / "retained"
    source = os.environ.get("DSA_RECOVERY_BASELINE")
    if source:
        assert Path(source).is_dir() and (Path(source) / "stock_analysis.db").is_file()
        shutil.copytree(source, root)
    else:
        root.mkdir()
        from src.services.thesis_ledger_control import ThesisLedgerControlStore
        ThesisLedgerControlStore(str(root / "stock_analysis.db"))
    path = root / "stock_analysis.db"
    rows = fingerprint_rows(path)
    files = {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
             for f in root.rglob("*") if f.is_file() and not f.name.startswith("stock_analysis.db")}
    return root, path, rows, files


def assert_preserved(root, path, rows, files, *, catalog_min_generation=None):
    with closing(sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        for table, (cols, old, generations) in rows.items():
            names = ",".join('"' + c.replace('"', '""') + '"' for c in cols)
            name = '"' + table.replace('"', '""') + '"'
            current = [hashlib.sha256(repr(tuple(r)).encode()).hexdigest()
                       for r in connection.execute("SELECT " + names + " FROM " + name)]
            permitted = set()
            if table == "thesis_ledger_catalog_generation" and catalog_min_generation is not None:
                permitted = {sha for generation, sha in generations.items() if generation < catalog_min_generation}
            assert set(old) - set(current) == permitted, "隔离副本的原记录变化：" + table
    for name, sha in files.items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == sha


def child_env(path):
    env = dict(os.environ)
    env.update({"DATABASE_PATH": str(path), "THESIS_LEDGER_FIXTURE_MODE": "true",
                "THESIS_LEDGER_DSA_SECRET_KEY": "dg2c-synthetic-encryption-key",
                "STOCK_INDEX_REMOTE_UPDATE_ENABLED": "false", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def test_catalog_cache_pruning_allowance_cannot_hide_other_loss(tmp_path):
    import pytest
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE thesis_ledger_catalog_generation(generation INTEGER PRIMARY KEY, value TEXT)")
        connection.executemany("INSERT INTO thesis_ledger_catalog_generation VALUES(?,?)", [(i, str(i)) for i in range(1, 6)])
        connection.execute("CREATE TABLE legacy(id INTEGER PRIMARY KEY, value TEXT)")
        connection.execute("INSERT INTO legacy VALUES(1,'preserved')")
    baseline = fingerprint_rows(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM thesis_ledger_catalog_generation WHERE generation=1")
    assert_preserved(tmp_path, path, baseline, {}, catalog_min_generation=2)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM thesis_ledger_catalog_generation WHERE generation=2")
    with pytest.raises(AssertionError, match="原记录变化"):
        assert_preserved(tmp_path, path, baseline, {}, catalog_min_generation=2)
    with sqlite3.connect(path) as connection:
        connection.execute("INSERT INTO thesis_ledger_catalog_generation VALUES(2,'2')")
        connection.execute("DELETE FROM legacy")
    with pytest.raises(AssertionError, match="legacy"):
        assert_preserved(tmp_path, path, baseline, {}, catalog_min_generation=2)


def interrupt(mode, path):
    process = subprocess.Popen([sys.executable, "-m", "tests.test_data_process_recovery", mode, str(path)],
                               env=child_env(path), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=15), "故障进程未到达持久化屏障"
            ready = json.loads(process.stdout.readline())
        process.kill()
        process.wait(timeout=5)
        assert process.returncode == -9
        return ready
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()


def test_catalog_process_crash_requeues_once_and_rejects_late_commit(tmp_path, monkeypatch, record_property):
    root, path, old_rows, old_files = prepare(tmp_path)
    ready = interrupt("catalog-interrupt", path)
    monkeypatch.setenv("DATABASE_PATH", str(path))
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    from src.services import thesis_ledger_control as control
    from src.services.thesis_ledger_catalog_worker import stop_catalog_worker
    later = (datetime.now(timezone.utc) + timedelta(seconds=control.CATALOG_JOB_LEASE_SECONDS + 1)).isoformat()
    monkeypatch.setattr(control, "_utc_now", lambda: later)
    store = control.ThesisLedgerControlStore(str(path))
    try:
        control._catalog_job_manager_for(str(path))
        deadline = time.monotonic() + 10
        while True:
            with store._connect() as connection:
                new = connection.execute("SELECT * FROM thesis_ledger_catalog_job WHERE id NOT IN ("
                                         + ",".join("?" for _ in ready["beforeIds"]) + ")",
                                         ready["beforeIds"]).fetchall()
            if len(new) == 2 and any(r["status"] == "succeeded" for r in new):
                break
            assert time.monotonic() < deadline, "目录重启后未收敛"
            Event().wait(0.02)
        original = store.get_catalog_job(ready["id"])
        assert original["status"] == "timeout"
        with store._connect() as connection:
            before_generation = connection.execute("SELECT count(*) FROM thesis_ledger_catalog_generation").fetchone()[0]
            original_raw = connection.execute("SELECT error_json FROM thesis_ledger_catalog_job WHERE id=?", (ready["id"],)).fetchone()
        assert json.loads(original_raw[0])["requeued"] is True
        stale = store._complete_catalog_job(ready["id"], ready["owner"], [], {})
        assert stale["status"] == "timeout"
        stop_catalog_worker(str(path))
        control._catalog_job_manager_for(str(path))
        stop_catalog_worker(str(path))
        with store._connect() as connection:
            assert connection.execute("SELECT count(*) FROM thesis_ledger_catalog_job").fetchone()[0] == len(ready["beforeIds"]) + 2
            assert connection.execute("SELECT count(*) FROM thesis_ledger_catalog_generation").fetchone()[0] == before_generation
            latest = connection.execute("SELECT max(generation) FROM thesis_ledger_catalog_generation").fetchone()[0]
        minimum = max(1, latest - 4)
        pruned = sum(g < minimum for g in old_rows["thesis_ledger_catalog_generation"][2])
        record_property("catalogPrunedOriginalGenerations", pruned)
        record_property("catalogJobsAfterCrash", 2)
        record_property("originalRows", sum(len(row[1]) for row in old_rows.values()))
        record_property("originalFiles", len(old_files))
        assert_preserved(root, path, old_rows, old_files, catalog_min_generation=minimum)
    finally:
        stop_catalog_worker(str(path))


def test_announcement_process_crash_recovers_lease_and_exhausts_attempts(tmp_path):
    root, path, old_rows, old_files = prepare(tmp_path)
    previous = interrupt("announcement-interrupt", path)
    from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
    now = [previous["leaseUntil"] + 1]
    jobs = AnnouncementJobs(root / "thesis-ledger-announcements", clock=lambda: now[0])
    recovered = jobs.claim()
    assert recovered["id"] == previous["id"] and recovered["attempt"] == 2
    assert recovered["owner"] != previous["owner"]
    assert jobs.finish(previous, {"errors": []}) is False
    assert jobs.finish(recovered, {"errors": [], "matched": 0}) is True
    assert jobs.claim() is None
    summary = jobs.ensure(["000001.SZ"], "2020-02-01", "2020-02-29", used=False)
    for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
        claimed = jobs.claim()
        assert claimed["attempt"] == attempt
        now[0] = claimed["leaseUntil"] + 1
    assert jobs.claim() is None
    assert jobs.read(summary["jobIds"][0])["state"] == "failed"
    assert jobs.claim() is None
    assert_preserved(root, path, old_rows, old_files)


def test_oauth_process_crash_recovers_pending_without_authorization(tmp_path):
    root, path, old_rows, old_files = prepare(tmp_path)
    ready = interrupt("oauth-interrupt", path)
    from src.services.provider_oauth_manager import ProviderOAuthManager
    from src.services.provider_oauth_store import ProviderOAuthStore
    repository = ProviderOAuthStore(str(path), encrypt=lambda _: ("fixture", "unused"))
    manager = ProviderOAuthManager(repository, builder_factory=lambda: (_ for _ in ()).throw(AssertionError("禁止授权")))
    assert manager.recover() == 1
    row = repository.get(ready["sessionId"])
    assert row["status"] == "failed" and row["errorCode"] == "OAUTH_RESTARTED"
    assert manager.recover() == 0 and manager.tasks == {}
    assert_preserved(root, path, old_rows, old_files)


def child(mode, path):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore
    store = ThesisLedgerControlStore(str(path))
    if mode == "catalog-interrupt":
        from src.services import thesis_ledger_catalog as catalog
        os.environ["THESIS_LEDGER_FIXTURE_MODE"] = "false"
        entered = Event()

        def block():
            entered.set()
            Event().wait(60)
            raise AssertionError("故障屏障未中断")

        catalog.build_catalog = block
        with store._connect() as connection:
            before = [r[0] for r in connection.execute("SELECT id FROM thesis_ledger_catalog_job")]
        row = store.trigger_catalog_job(owner="dg2c-interrupted-owner")
        assert entered.wait(10)
        value = {"id": row["id"], "owner": "dg2c-interrupted-owner", "beforeIds": before}
    elif mode == "announcement-interrupt":
        from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
        jobs = AnnouncementJobs(path.parent / "thesis-ledger-announcements")
        jobs.ensure(["000001.SZ"], "2020-01-01", "2020-01-31", used=False)
        value = jobs.claim()
    elif mode == "oauth-interrupt":
        from src.services.provider_oauth_store import ProviderOAuthStore
        repository = ProviderOAuthStore(str(path), encrypt=lambda _: ("fixture", "unused"))
        value, _ = repository.create("dg2c-controlled-client")
    else:
        raise ValueError("未知中断场景")
    print(json.dumps(value), flush=True)
    Event().wait(60)
    raise AssertionError("父进程未执行中断")


if __name__ == "__main__":
    child(sys.argv[1], Path(sys.argv[2]))
