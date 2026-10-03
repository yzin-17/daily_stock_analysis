"""A legacy policy can be retired only with a matching target and backup."""

import json
import sqlite3

import pytest

from scripts.rebase_legacy_thesis_ledger_policy import rebase


def _database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE thesis_ledger_policy_state (
                consumer TEXT PRIMARY KEY, revision INTEGER, routes_json TEXT,
                effective_json TEXT
            );
            CREATE TABLE thesis_ledger_policy_history (consumer TEXT, revision INTEGER);
            CREATE TABLE thesis_ledger_route_admission_v3 (consumer TEXT, evidence_ref TEXT);
            CREATE TABLE thesis_ledger_provider_config (provider_id TEXT);
        """)
        connection.execute(
            "INSERT INTO thesis_ledger_policy_state VALUES (?, ?, ?, ?)",
            ("thesis-ledger", 30, json.dumps({"routesV3": []}),
             json.dumps({"contractVersion": 2})),
        )
        connection.execute("INSERT INTO thesis_ledger_policy_history VALUES (?, ?)",
                           ("thesis-ledger", 30))
        connection.execute("INSERT INTO thesis_ledger_route_admission_v3 VALUES (?, ?)",
                           ("thesis-ledger", "sha256:fixture"))
        connection.execute("INSERT INTO thesis_ledger_provider_config VALUES (?)", ("hithink",))


def test_rebase_keeps_history_admission_and_provider(tmp_path):
    database = tmp_path / "control.db"
    backup = tmp_path / "backup.db"
    _database(database)
    confirmation = f"{database.resolve()}:thesis-ledger:30"

    assert rebase(database, backup, confirmation) == 30
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM thesis_ledger_policy_state").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM thesis_ledger_policy_history").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM thesis_ledger_route_admission_v3").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM thesis_ledger_provider_config").fetchone()[0] == 1
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT revision FROM thesis_ledger_policy_state").fetchone()[0] == 30
    assert backup.stat().st_mode & 0o777 == 0o600


def test_rebase_refuses_wrong_revision_without_backup(tmp_path):
    database = tmp_path / "control.db"
    backup = tmp_path / "backup.db"
    _database(database)

    with pytest.raises(ValueError, match="revision"):
        rebase(database, backup, f"{database.resolve()}:thesis-ledger:29")
    assert not backup.exists()
