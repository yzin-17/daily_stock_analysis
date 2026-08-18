"""Catalog Job lease、去重和旧 SQLite schema 升级回归。"""

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.services.thesis_ledger_control import ThesisLedgerControlStore


def _wait_terminal(store: ThesisLedgerControlStore, job_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = store.get_catalog_job(job_id)
        if result["status"] in {"succeeded", "failed", "timeout"}:
            return result
        time.sleep(0.01)
    raise AssertionError(f"Catalog Job {job_id} did not reach a terminal state")


def test_catalog_job_claim_exposes_owner_lease_and_update_time(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    store = ThesisLedgerControlStore(str(tmp_path / "catalog.db"))

    result = store.trigger_catalog_job(owner="manual-trigger")

    assert result["status"] in {"pending", "running"}
    result = _wait_terminal(store, result["id"])
    assert result["status"] == "succeeded"
    assert result["owner"] == "manual-trigger"
    assert result["leaseExpiresAt"]
    assert result["leaseValid"] is False
    assert result["retryable"] is False
    assert result["createdAt"]
    assert result["updatedAt"]
    assert result["updatedAt"] >= result["createdAt"]


def test_valid_running_job_dedupes_manual_and_scheduled_triggers(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    database_path = str(tmp_path / "catalog-dedupe.db")
    first_store = ThesisLedgerControlStore(database_path)
    second_store = ThesisLedgerControlStore(database_path)
    entered = threading.Event()
    release = threading.Event()
    original_complete = ThesisLedgerControlStore._complete_catalog_job

    def delayed_complete(self, job_id, owner, items, failures):
        if owner == "manual-trigger":
            entered.set()
            assert release.wait(timeout=5)
        return original_complete(self, job_id, owner, items, failures)

    monkeypatch.setattr(ThesisLedgerControlStore, "_complete_catalog_job", delayed_complete)
    first_result = {}
    worker = threading.Thread(
        target=lambda: first_result.setdefault(
            "value", first_store.trigger_catalog_job(owner="manual-trigger")
        )
    )
    worker.start()
    assert entered.wait(timeout=5)

    scheduled_result = second_store.trigger_catalog_job(owner="scheduled-trigger")

    assert scheduled_result["status"] == "running"
    assert scheduled_result["owner"] == "manual-trigger"
    assert scheduled_result["leaseValid"] is True
    assert scheduled_result["id"] != ""
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    completed = _wait_terminal(first_store, first_result["value"]["id"])
    assert completed["status"] == "succeeded"
    assert first_result["value"]["id"] == scheduled_result["id"]


def test_expired_running_job_is_timed_out_and_retried_after_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    database_path = str(tmp_path / "catalog-restart.db")
    expired_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    created_at = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE thesis_ledger_catalog_job (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                generation INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                error_json TEXT,
                owner TEXT,
                lease_expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO thesis_ledger_catalog_job
            (id, status, generation, checksum, error_json, owner,
             lease_expires_at, created_at, updated_at)
            VALUES ('crashed-job', 'running', 1, '', NULL, 'crashed-process', ?, ?, ?)
            """,
            (expired_at, created_at, created_at),
        )
        connection.commit()

    restarted_store = ThesisLedgerControlStore(database_path)
    retried = restarted_store.trigger_catalog_job(owner="restarted-process")
    retried = _wait_terminal(restarted_store, retried["id"])

    assert retried["status"] == "succeeded"
    assert retried["owner"] == "restarted-process"
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        timed_out = connection.execute(
            "SELECT * FROM thesis_ledger_catalog_job WHERE id='crashed-job'"
        ).fetchone()
        assert timed_out["status"] == "timeout"
        assert timed_out["owner"] == "crashed-process"
        assert timed_out["updated_at"] > timed_out["created_at"]


def test_old_catalog_job_schema_upgrade_is_idempotent(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    database_path = str(tmp_path / "catalog-legacy.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE thesis_ledger_catalog_job (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                generation INTEGER NOT NULL,
                checksum TEXT NOT NULL,
                error_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO thesis_ledger_catalog_job
            (id, status, generation, checksum, error_json, created_at)
            VALUES ('legacy-job', 'running', 1, '', NULL, '2026-01-01T00:00:00+00:00')
            """
        )
        connection.commit()

    first_store = ThesisLedgerControlStore(database_path)
    second_store = ThesisLedgerControlStore(database_path)
    result = second_store.trigger_catalog_job(owner="legacy-recovery")
    result = _wait_terminal(second_store, result["id"])

    assert result["status"] == "succeeded"
    assert result["owner"] == "legacy-recovery"
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(thesis_ledger_catalog_job)")
        }
        assert {"owner", "lease_expires_at", "updated_at"} <= columns
        legacy = connection.execute(
            "SELECT status, updated_at FROM thesis_ledger_catalog_job WHERE id='legacy-job'"
        ).fetchone()
        assert legacy[0] == "timeout"
        assert legacy[1]


def test_control_trigger_returns_before_provider_and_status_is_observable(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "catalog-api.db"))
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    entered = threading.Event()
    release = threading.Event()

    def blocked_catalog():
        entered.set()
        assert release.wait(timeout=5)
        return [{
            "canonicalCode": "600519",
            "instrumentType": "STOCK",
            "market": "SH",
            "displayName": "贵州茅台",
        }], {}

    import src.services.thesis_ledger_catalog as catalog_module

    monkeypatch.setattr(catalog_module, "build_catalog", blocked_catalog)
    from api.thesis_ledger import router

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    client = TestClient(app)
    started = time.monotonic()
    response = client.post(
        "/api/v1/thesis-ledger/control/catalog/jobs",
        headers={"authorization": "Bearer control-token"},
        json={
            "contractVersion": 1,
            "consumer": "thesis-ledger",
            "requestId": "async-catalog-test",
        },
    )
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    job = response.json()
    assert job["status"] in {"pending", "running"}
    assert elapsed < 1
    assert entered.wait(timeout=5)

    status = client.get(
        f"/api/v1/thesis-ledger/control/catalog/jobs/{job['id']}",
        headers={"authorization": "Bearer control-token"},
    )
    assert status.status_code == 200
    assert status.json()["status"] == "running"
    release.set()
    completed = _wait_terminal(ThesisLedgerControlStore(str(tmp_path / "catalog-api.db")), job["id"])
    assert completed["status"] == "succeeded"


def test_catalog_worker_restarts_from_pending_job_without_duplicate_generation(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    database_path = str(tmp_path / "catalog-worker-restart.db")
    store = ThesisLedgerControlStore(database_path)
    pending, created = store._claim_catalog_job("previous-worker", initial_status="pending")
    assert created is True

    import src.services.thesis_ledger_control as control_module

    with control_module._catalog_job_managers_lock:
        control_module._catalog_job_managers.pop(database_path, None)
    control_module._catalog_job_manager_for(database_path)

    completed = _wait_terminal(store, pending["id"])
    assert completed["status"] == "succeeded"
    with sqlite3.connect(database_path) as connection:
        generation_count = connection.execute(
            "SELECT COUNT(*) FROM thesis_ledger_catalog_generation WHERE generation = ?",
            (completed["generation"],),
        ).fetchone()[0]
    assert generation_count == 1
