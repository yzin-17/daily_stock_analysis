"""当前 Catalog 身份、事务 ACK 与旧游标拒绝。"""

import sqlite3
import threading

import pytest

from src.services.thesis_ledger_control import ThesisLedgerControlStore, ControlContractError
from src.services import thesis_ledger_catalog_contract as contract


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    return ThesisLedgerControlStore(str(tmp_path / "catalog.db"))


def ack(snapshot, **changes):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "ack-request",
            "generation": snapshot["generation"], "checksum": snapshot["checksum"], **changes}


@pytest.mark.parametrize("cursor", ["0", "generation:0", "v2:1", "generation:999"])
def test_old_cursor_does_not_upgrade_catalog(store, cursor):
    before = store.catalog_snapshot()
    for read in (store.catalog_snapshot, store.catalog_delta):
        with pytest.raises(ControlContractError) as error:
            read(cursor)
        assert error.value.code == "CATALOG_CURSOR_EXPIRED"
    assert store.catalog_snapshot() == before


@pytest.mark.parametrize("changes", [{"contractVersion": 1}, {"contractVersion": 2},
                                     {"requestId": ""}, {"consumer": "other"}, {"legacy": True}])
def test_old_ack_does_not_write(store, changes):
    snapshot = store.catalog_snapshot()
    with pytest.raises(ControlContractError):
        store.catalog_ack(ack(snapshot, **changes))
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM thesis_ledger_catalog_ack").fetchone()[0] == 0


def test_ack_returns_current_bound_envelope(store):
    snapshot = store.catalog_snapshot()
    result = store.catalog_ack(ack(snapshot))
    assert result == {**ack(snapshot), "cursor": snapshot["cursor"], "acknowledged": True}
    with pytest.raises(ControlContractError):
        store.catalog_ack(ack(snapshot, checksum="b" * 64))
    assert store.catalog_delta(snapshot["cursor"])["deleted"] == []


def test_corrupt_stored_catalog_is_not_relabelled_current(store):
    store.catalog_snapshot()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE thesis_ledger_catalog_generation SET checksum=?", ("b" * 64,))
    with pytest.raises(ControlContractError) as error:
        store.catalog_snapshot()
    assert error.value.code == "CATALOG_INVALID_STATE"


def test_ack_identity_check_holds_write_lock_until_commit(store, monkeypatch):
    snapshot = store.catalog_snapshot()
    checked = threading.Event()
    publisher_started = threading.Event()
    published = threading.Event()
    original = contract.current_catalog_row

    def guarded(row):
        result = original(row)
        checked.set()
        assert publisher_started.wait(3)
        assert not published.wait(0.1)
        return result

    monkeypatch.setattr(contract, "current_catalog_row", guarded)

    def publish():
        assert checked.wait(3)
        with sqlite3.connect(store.database_path) as connection:
            publisher_started.set()
            connection.execute("INSERT INTO thesis_ledger_catalog_generation SELECT 2, checksum, 'generation:2', complete, items_json, created_at FROM thesis_ledger_catalog_generation WHERE generation=1")
            connection.commit()
        published.set()

    worker = threading.Thread(target=publish)
    worker.start()
    assert store.catalog_ack(ack(snapshot))["generation"] == 1
    worker.join(3)
    assert not worker.is_alive() and published.is_set()
    monkeypatch.setattr(contract, "current_catalog_row", original)
    with pytest.raises(ControlContractError) as error:
        store.catalog_ack(ack(snapshot))
    assert error.value.code == "CATALOG_CHECKSUM_MISMATCH"


def test_late_owner_cannot_publish_after_lease_timeout(store):
    row, claimed = store._claim_catalog_job("old-owner", initial_status="pending")
    assert claimed
    store._start_catalog_job(row["id"], "old-owner")
    with sqlite3.connect(store.database_path) as connection:
        connection.execute("UPDATE thesis_ledger_catalog_job SET lease_expires_at='2020-01-01T00:00:00+00:00' WHERE id=?", (row["id"],))
    assert store.get_catalog_job(row["id"])["status"] == "timeout"
    late = store._complete_catalog_job(row["id"], "old-owner", [], {})
    assert late["status"] == "timeout"
    with sqlite3.connect(store.database_path) as connection:
        assert connection.execute("SELECT count(*) FROM thesis_ledger_catalog_generation").fetchone()[0] == 0
