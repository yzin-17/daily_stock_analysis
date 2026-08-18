"""DSA Provider credential encryption and non-destructive key rotation tests."""

import sqlite3

import pytest

from src.services import thesis_ledger_control as control_module
from src.services.thesis_ledger_control import ControlContractError, ThesisLedgerControlStore


def _credential_row(database_path: str):
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT credential_ciphertext, secret_key_version
            FROM thesis_ledger_provider_config
            WHERE provider_id='akshare'
            """
        ).fetchone()


def test_provider_credentials_are_reencrypted_with_retained_previous_key(
    monkeypatch, tmp_path
):
    database_path = str(tmp_path / "credential-rotation.db")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "old-secret")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "v1")
    monkeypatch.delenv("THESIS_LEDGER_DSA_SECRET_KEY_PREVIOUS", raising=False)
    ThesisLedgerControlStore(database_path).save_provider_config(
        "akshare", {"credential": "provider-secret", "settings": {}}
    )
    old_row = _credential_row(database_path)

    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "new-secret")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "v2")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_PREVIOUS", "old-secret")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_PREVIOUS_VERSION", "v1")
    ThesisLedgerControlStore(database_path)
    rotated_row = _credential_row(database_path)

    assert old_row["secret_key_version"] == "v1"
    assert rotated_row["secret_key_version"] == "v2"
    assert rotated_row["credential_ciphertext"] != old_row["credential_ciphertext"]
    assert (
        control_module._decrypt_secret(
            rotated_row["secret_key_version"], rotated_row["credential_ciphertext"]
        )
        == "provider-secret"
    )


def test_rotation_defers_without_previous_key_and_keeps_old_ciphertext(monkeypatch, tmp_path):
    database_path = str(tmp_path / "credential-rotation-deferred.db")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "old-secret")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "v1")
    ThesisLedgerControlStore(database_path).save_provider_config(
        "akshare", {"credential": "provider-secret", "settings": {}}
    )
    old_row = _credential_row(database_path)

    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "new-secret")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", "v2")
    monkeypatch.delenv("THESIS_LEDGER_DSA_SECRET_KEY_PREVIOUS", raising=False)
    ThesisLedgerControlStore(database_path)
    deferred_row = _credential_row(database_path)

    assert dict(deferred_row) == dict(old_row)
    with pytest.raises(ControlContractError) as raised:
        control_module._decrypt_secret(
            deferred_row["secret_key_version"], deferred_row["credential_ciphertext"]
        )
    assert raised.value.code == "SECRET_KEY_UNAVAILABLE"
