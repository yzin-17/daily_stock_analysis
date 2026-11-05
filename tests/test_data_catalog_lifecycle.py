"""目录工作单元停止、实际隔离进程回收和持久任务恢复。"""

import multiprocessing
from threading import Event, enumerate as enumerate_threads
import time

import pytest

from src.services.thesis_ledger_catalog import _bounded_call
from src.services.thesis_ledger_catalog_execution import catalog_checkpoint
from src.services.thesis_ledger_catalog_worker import CatalogJobWorker, stop_catalog_worker
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _catalog_job_manager_for


def slow_provider():
    time.sleep(30)
    return []


class EmptyStore:
    def __init__(self, _path):
        self.released = []

    def recover_catalog_jobs(self, owner):
        return []

    def execute_catalog_job(self, job_id, owner):
        pass

    def release_catalog_job_on_stop(self, job_id, owner):
        self.released.append((job_id, owner))


def test_idle_worker_stops_idempotently_and_rejects_new_in_memory_work():
    worker = CatalogJobWorker("controlled", EmptyStore)
    worker.stop()
    worker.stop()
    assert not worker._thread.is_alive() and worker._queue.unfinished_tasks == 0
    with pytest.raises(RuntimeError, match="正在停止"):
        worker.enqueue("pending-job", "owner")


def test_failed_initial_recovery_never_leaves_a_thread():
    class FailingStore(EmptyStore):
        def recover_catalog_jobs(self, owner):
            raise RuntimeError("controlled-storage-failure")
    before = set(enumerate_threads())
    with pytest.raises(RuntimeError, match="controlled-storage-failure"):
        CatalogJobWorker("controlled", FailingStore)
    assert set(enumerate_threads()) == before


def test_stop_terminates_real_provider_process_and_releases_capacity():
    class BlockingStore(EmptyStore):
        def execute_catalog_job(self, job_id, owner):
            _bounded_call(slow_provider, timeout_seconds=30, max_attempts=1)
    baseline = {process.pid for process in multiprocessing.active_children()}
    worker = CatalogJobWorker("controlled", BlockingStore)
    worker.enqueue("job", "owner")
    process = None
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            process = next((child for child in multiprocessing.active_children()
                            if child.pid not in baseline and child.name == "catalog-provider"), None)
            if process:
                break
            Event().wait(0.01)
        assert process is not None
        worker.stop()
        assert not process.is_alive() and not worker._thread.is_alive()
        assert worker.store.released == [("job", "owner")]
        from src.services import thesis_ledger_catalog as catalog
        slots = catalog._CATALOG_PROVIDER_WORKER_SLOTS
        assert slots.acquire(blocking=False)
        assert slots.acquire(blocking=False)
        slots.release()
        slots.release()
    finally:
        worker.stop()


def test_running_job_becomes_pending_and_restarts_without_duplicate_generation(monkeypatch, tmp_path):
    from src.services import thesis_ledger_catalog as catalog
    path = str(tmp_path / "catalog.db")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    entered = Event()

    def cancellable_build():
        entered.set()
        while True:
            catalog_checkpoint()
            Event().wait(0.01)

    monkeypatch.setattr(catalog, "build_catalog", cancellable_build)
    store = ThesisLedgerControlStore(path)
    job = store.trigger_catalog_job(owner="original-owner")
    try:
        assert entered.wait(5)
        stop_catalog_worker(path)
        stopped = store.get_catalog_job(job["id"])
        assert stopped["status"] == "pending" and stopped["owner"] == "original-owner"
        assert stopped["leaseExpiresAt"] is None
        monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
        _catalog_job_manager_for(path)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = store.get_catalog_job(job["id"])
            if result["status"] == "succeeded":
                break
            Event().wait(0.01)
        assert result["status"] == "succeeded"
        with store._connect() as connection:
            assert connection.execute("SELECT COUNT(*) FROM thesis_ledger_catalog_generation").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM thesis_ledger_catalog_job").fetchone()[0] == 1
    finally:
        stop_catalog_worker(path)


def test_stop_release_cannot_overwrite_other_owner(monkeypatch, tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "catalog.db"))
    job, created = store._claim_catalog_job("current-owner", initial_status="pending")
    assert created
    store._start_catalog_job(job["id"], "current-owner")
    store.release_catalog_job_on_stop(job["id"], "old-owner")
    assert store.get_catalog_job(job["id"])["status"] == "running"
    store.release_catalog_job_on_stop(job["id"], "current-owner")
    assert store.get_catalog_job(job["id"])["status"] == "pending"


def test_unresponsive_execution_does_not_report_clean_stop():
    entered, release = Event(), Event()

    class UnresponsiveStore(EmptyStore):
        def execute_catalog_job(self, job_id, owner):
            entered.set()
            release.wait(5)

    worker = CatalogJobWorker("controlled", UnresponsiveStore)
    worker.enqueue("job", "owner")
    try:
        assert entered.wait(5)
        with pytest.raises(RuntimeError, match="退出期限"):
            worker.stop(timeout=0.01)
        assert worker._thread.is_alive()
    finally:
        release.set()
        worker.stop()
