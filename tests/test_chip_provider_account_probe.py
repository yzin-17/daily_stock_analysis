"""筹码账号诊断必须脱敏，并且以只读连接读取现有控制配置。"""

import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from scripts import probe_chip_provider_accounts as probe


def test_rejected_response_does_not_expose_credentials_or_error_text(monkeypatch):
    calls = []

    def get(url, **options):
        calls.append((url, options))
        return SimpleNamespace(status_code=401, json=lambda: {
            "ret_code": -12006, "ret_msg": "secret-in-upstream-error", "data": None,
        })

    monkeypatch.setattr(probe.requests, "get", get)
    result = probe.request_summary("https://example.invalid/chip", headers={"X-api-key": "private-key"})
    assert result == {"httpStatus": 401, "businessCode": -12006, "result": "rejected"}
    assert calls[0][1]["allow_redirects"] is False
    assert calls[0][1]["timeout"] == (3, 6)
    assert "private-key" not in json.dumps(result)
    assert "secret-in-upstream-error" not in json.dumps(result)


def test_success_exposes_only_public_field_names_and_counts(monkeypatch):
    monkeypatch.setattr(probe.requests, "get", lambda *args, **kwargs: SimpleNamespace(
        status_code=200, json=lambda: {"code": 0, "data": {
            "dates": ["2026-11-09"], "latest_date": "2026-11-09", "access_token": "private-token",
        }},
    ))
    result = probe.request_summary("https://example.invalid/chip")
    assert result["result"] == "success"
    assert result["dataFields"] == ["dates", "latest_date"]
    assert result["datesCount"] == 1
    assert "private-token" not in json.dumps(result)


def test_network_failure_does_not_expose_exception_url(monkeypatch):
    def get(*args, **kwargs):
        raise probe.requests.RequestException("https://example.invalid?token=private-token")

    monkeypatch.setattr(probe.requests, "get", get)
    assert probe.request_summary("https://example.invalid/chip") == {"result": "network_failure"}


def test_snapshot_reader_does_not_initialize_or_write_database(monkeypatch, tmp_path):
    database = tmp_path / "control.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE probe (value TEXT)")
        connection.execute("INSERT INTO probe VALUES ('existing')")
    before = database.read_bytes()

    def snapshot(self, provider):
        with self._connect() as connection:
            assert connection.execute("SELECT value FROM probe").fetchone()[0] == "existing"
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("INSERT INTO probe VALUES ('forbidden')")
        return provider

    class ControlStore:
        def __init__(self, *args, **kwargs):
            raise AssertionError("不能初始化控制库或轮换凭据")

        provider_credential_snapshot = snapshot

    monkeypatch.setitem(sys.modules, "src.services.thesis_ledger_control", SimpleNamespace(
        ThesisLedgerControlStore=ControlStore, _database_path=lambda: str(database),
    ))
    assert probe.read_snapshot("futu") == "futu"
    assert database.read_bytes() == before
    assert {path.name for path in Path(tmp_path).iterdir()} == {"control.db"}
