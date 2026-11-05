"""控制、OAuth 与公告存储的资源关闭和独立重开。"""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest


def test_control_connections_close_explicitly_after_each_context(tmp_path, monkeypatch):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore
    original_connect = sqlite3.connect
    connections = []

    class TrackedConnection(sqlite3.Connection):
        explicitly_closed = False

        def close(self):
            self.explicitly_closed = True
            super().close()

    def connect(*args, **kwargs):
        connection = original_connect(*args, factory=TrackedConnection, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    try:
        store = ThesisLedgerControlStore(str(tmp_path / "control.db"))
        with store._connect() as connection:
            connection.execute("SELECT 1")
        assert connections
        assert all(connection.explicitly_closed for connection in connections)
    finally:
        for connection in connections:
            connection.close()


@pytest.mark.parametrize("fail", [False, True])
def test_control_transaction_closes_on_success_or_rollback(tmp_path, fail):
    from src.services.thesis_ledger_control_sqlite import control_connection
    path = str(tmp_path / "transaction.db")
    with control_connection(path) as connection:
        connection.execute("CREATE TABLE fixture (id INTEGER PRIMARY KEY)")
    try:
        with control_connection(path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO fixture VALUES (1)")
            if fail:
                raise ValueError("controlled-rollback")
    except ValueError:
        assert fail
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connection.execute("SELECT 1")
    with control_connection(path) as reopened:
        assert reopened.execute("SELECT COUNT(*) FROM fixture").fetchone()[0] == (0 if fail else 1)


STORAGE_PROBE = r"""
import importlib.abc, json, os, socket, sys
from pathlib import Path
forbidden = ("src.config", "src.storage", "src.agent", "src.llm", "src.notification", "src.report_language",
             "src.schemas.decision_scale", "src.stock_analyzer", "litellm", "openai")
class BlockProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("data storage imported " + fullname)
sys.meta_path.insert(0, BlockProducts())
def deny(self, address):
    raise AssertionError("data storage attempted external connection")
socket.socket.connect = deny
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _encrypt_secret
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.thesis_ledger_announcements import AnnouncementRepository, announcement_root
from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
path = os.environ["DATABASE_PATH"]
control = ThesisLedgerControlStore(path)
control.save_provider_config("tickflow", {"enabled":False, "credentials":{
    "method":"api_key", "values":{"apiKey":"fixture-encrypted-private-value"}}})
before = control.provider_credential_snapshot("tickflow")
assert before.values == {"apiKey":"fixture-encrypted-private-value"}
with control._connect() as connection:
    assert connection.execute("SELECT enabled FROM thesis_ledger_provider_config WHERE provider_id='tickflow'").fetchone()[0] == 0
now = [1800000000]
oauth = ProviderOAuthStore(path, encrypt=_encrypt_secret, clock=lambda:now[0])
control.save_provider_config("longbridge", {"enabled":False})
session, created = oauth.create("fixture-client")
assert created
token = json.dumps({"client_id":"fixture-client", "access_token":"fixture-access",
                   "refresh_token":"fixture-refresh", "expires_at":1900000000})
version = oauth.save_authorized_token(session["sessionId"], token)
reopened = ThesisLedgerControlStore(path)
after = reopened.provider_credential_snapshot("tickflow")
assert before.values == after.values and before.credential_version == after.credential_version
with reopened._connect() as connection:
    rows = connection.execute("SELECT credential_ciphertext FROM thesis_ledger_provider_config").fetchall()
    assert "fixture-encrypted-private-value" not in repr(rows) and "fixture-access" not in repr(rows)
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    assert len(tables) == 11 and all(name.startswith("thesis_ledger_") for name in tables)
restored_oauth = ProviderOAuthStore(path, encrypt=_encrypt_secret, clock=lambda:now[0])
assert restored_oauth.get(session["sessionId"])["status"] == "succeeded"
assert reopened.provider_credential_snapshot("longbridge").credential_version == version
assert reopened.provider_credential_snapshot("longbridge").values["tokenJson"] == token
with reopened._connect() as connection:
    assert connection.execute("SELECT enabled FROM thesis_ledger_provider_config WHERE provider_id='longbridge'").fetchone()[0] == 0
root = announcement_root()
assert root == Path(path).resolve().parent / "thesis-ledger-announcements"
repo = AnnouncementRepository()
raw = b"%PDF-1.4 controlled-original-preservation"
digest = repo.save_original(raw)
record = {"id":"fixture-ann", "symbol":"000995.SZ", "title":"受控公告", "url":"https://controlled.invalid/ann",
          "publishedDate":"2025-01-02", "uses":["risk"], "downloadState":"ready", "parseState":"parsed", "sha256":digest}
repo.save(record)
assert AnnouncementRepository().original(record) == raw
record_bytes = (root / "fixture-ann.json").read_bytes()
jobs = AnnouncementJobs(root, clock=lambda:now[0])
queued = jobs.ensure(["000995.SZ"], "2025-01-01", "2025-01-31")
first = jobs.claim()
assert first and first["attempt"] == 1
now[0] += jobs.LEASE_SECONDS + 1
restored_jobs = AnnouncementJobs(root, clock=lambda:now[0])
second = restored_jobs.claim()
assert second["id"] == first["id"] and second["owner"] != first["owner"] and second["attempt"] == 2
result = {"matched":1,"downloaded":1,"parsed":1,"errors":[]}
assert jobs.finish(first,result) is False
assert restored_jobs.finish(second,result) is True
assert restored_jobs.summary(queued["jobIds"])["status"] == "ready"
assert restored_jobs.root == root / ".acquisition"
assert (root / "fixture-ann.json").read_bytes() == record_bytes
assert AnnouncementRepository().original(record) == raw
assert not any(name == p or name.startswith(p + ".") for name in sys.modules for p in forbidden)
print("data-storage-reopen-ok")
"""


def test_encrypted_control_oauth_and_announcement_stores_reopen_without_products(tmp_path):
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    result = subprocess.run(
        [sys.executable, "-c", STORAGE_PROBE], cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "ENV_FILE": str(env_file),
             "DATABASE_PATH": str(tmp_path / "shared.db"), "THESIS_LEDGER_DSA_SECRET_KEY": "fixture-storage-secret"},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr[-1800:]
    assert "data-storage-reopen-ok" in result.stdout
