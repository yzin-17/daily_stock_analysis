"""实际 Control HTTP/SQLite 的 RQData 账号只写、修订和读取接缝。"""

import json
import sqlite3
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient as HttpClient
import pytest

from api.thesis_ledger import router_v3
from src.services.provider_credential_revision import provider_credential_revision
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_rqdata_read import read_rqdata_fund_event_with_credentials


USERNAME = "synthetic-rqdata-account"
PASSWORD = " synthetic-rqdata-password "
HEADERS = {"authorization": "Bearer synthetic-control-token"}
CONFIG_URL = "/api/v3/thesis-ledger/control/providers/rqdata/config"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    database = tmp_path / "control.db"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "synthetic-control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-data-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-encryption-key-for-isolated-tests")
    monkeypatch.setenv("RQDATA_USERNAME", "unused-environment-account")
    monkeypatch.setenv("RQDATA_PASSWORD", "unused-environment-password")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    with HttpClient(app) as client:
        yield client, ThesisLedgerControlStore(str(database)), database


def post(client, **values):
    return client.post(CONFIG_URL, headers=HEADERS, json={
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "rqdata-credentials",
        **values,
    })


def save(client, **values):
    return post(client, credentials={
        "method": "username_password", "values": {"username": USERNAME, "password": PASSWORD, **values},
    })


def revision(snapshot):
    return provider_credential_revision(snapshot, "synthetic-v1", b"synthetic-admission-key")


def assert_redacted(value):
    serialized = json.dumps(value)
    assert USERNAME not in serialized
    assert "synthetic-rqdata-password" not in serialized
    assert "unused-environment" not in serialized


def test_registered_provider_has_only_write_only_account_fields_and_no_routes(runtime):
    client, _store, _database = runtime
    response = client.get("/api/v3/thesis-ledger/control/providers", headers=HEADERS)
    assert response.status_code == 200
    provider = next(item for item in response.json()["providers"] if item["providerId"] == "rqdata")
    assert provider["requiresCredential"] is True
    assert provider["credentialConfigured"] is False
    assert provider["credentialSource"] == "none"
    assert provider["capabilities"] == {}
    assert provider["upstreamSources"][0]["capabilities"] == {}
    assert provider["credentialSchema"]["methods"] == [{
        "method": "username_password", "fields": [
            {"name": "username", "secret": True, "required": True},
            {"name": "password", "secret": True, "required": True},
        ],
    }]
    assert_redacted(response.json())


def test_actual_http_save_encrypts_account_and_reopened_store_preserves_password(runtime):
    client, store, database = runtime
    response = save(client)
    assert response.status_code == 200
    assert response.json()["credentialConfigured"] is True
    assert response.json()["credentialMethod"] == "username_password"
    assert_redacted(response.json())
    with sqlite3.connect(database) as connection:
        ciphertext, version = connection.execute(
            "SELECT credential_ciphertext, credential_version FROM thesis_ledger_provider_config WHERE provider_id='rqdata'",
        ).fetchone()
    assert_redacted(ciphertext)
    assert version == 1
    snapshot = ThesisLedgerControlStore(str(database)).provider_credential_snapshot("rqdata")
    assert snapshot.source == "control"
    assert snapshot.method == "username_password"
    assert snapshot.values == {"username": USERNAME, "password": PASSWORD}
    assert USERNAME not in repr(snapshot)
    assert revision(snapshot) == revision(store.provider_credential_snapshot("rqdata"))
    with pytest.raises(TypeError):
        snapshot.values["username"] = "other-account"


def test_omitted_and_blank_updates_keep_account_but_rotation_changes_revision(runtime):
    client, store, _database = runtime
    assert save(client).status_code == 200
    original = store.provider_credential_snapshot("rqdata")
    assert post(client, enabled=False).status_code == 200
    assert save(client, username="", password="  ").status_code == 200
    retained = store.provider_credential_snapshot("rqdata")
    assert retained.values == original.values
    assert retained.credential_version == original.credential_version
    assert revision(retained) == revision(original)
    rotated = post(client, credentials={"method": "username_password", "values": {"password": "rotated-password"}})
    assert rotated.status_code == 200
    assert rotated.json()["enabled"] is False
    changed = store.provider_credential_snapshot("rqdata")
    assert changed.values["username"] == USERNAME
    assert changed.credential_version == original.credential_version + 1
    assert revision(changed) != revision(original)


@pytest.mark.parametrize("values", [
    {"username": USERNAME}, {"password": PASSWORD}, {"username": USERNAME, "password": "   "},
    {"username": USERNAME, "password": PASSWORD, "token": "invalid-field"},
])
def test_partial_or_unknown_initial_accounts_do_not_write_a_config(runtime, values):
    client, store, database = runtime
    response = post(client, credentials={"method": "username_password", "values": values})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_PROVIDER_CREDENTIALS"
    assert_redacted(response.json())
    assert revision(store.provider_credential_snapshot("rqdata")) is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM thesis_ledger_provider_config WHERE provider_id='rqdata'").fetchone()[0] == 0


def test_plaintext_legacy_input_and_data_token_are_rejected(runtime):
    client, store, _database = runtime
    legacy = post(client, credential="legacy-account-string")
    assert legacy.status_code == 422
    assert legacy.json()["detail"]["code"] == "INVALID_PROVIDER_CREDENTIALS"
    unauthorized = client.post(CONFIG_URL, headers={"authorization": "Bearer synthetic-data-token"}, json={})
    assert unauthorized.status_code == 401
    assert revision(store.provider_credential_snapshot("rqdata")) is None


def test_control_and_environment_account_revisions_are_distinct(runtime):
    client, store, _database = runtime
    assert save(client).status_code == 200
    snapshot = store.provider_credential_snapshot("rqdata")
    environment = ProviderCredentialSnapshot.create("rqdata", "environment", snapshot.method, snapshot.values, 1, 1)
    assert revision(snapshot) != revision(environment)
    for provider_id, method, values in [
        ("hithink", "api_key", {"apiKey": "synthetic-key"}),
        ("tushare", "token", {"token": "synthetic-token", "httpUrl": "https://synthetic.invalid"}),
    ]:
        assert revision(ProviderCredentialSnapshot.create(provider_id, "control", method, values, 1, 1)) is None


@pytest.mark.parametrize("change", ["none", "rotate", "clear", "remove"])
def test_actual_store_account_is_checked_before_and_after_isolated_read(runtime, monkeypatch, change):
    client, store, _database = runtime
    assert save(client).status_code == 200
    admitted = revision(store.provider_credential_snapshot("rqdata"))

    def execute(factory, kind, symbol, **_options):
        assert (factory.username, factory.password) == (USERNAME, PASSWORD)
        assert (kind, symbol) == ("split", "000246.OF")
        if change == "rotate":
            assert save(client, password="rotated-password").status_code == 200
        elif change == "clear":
            assert post(client, clearCredentials=True).status_code == 200
        elif change == "remove":
            store.remove_provider("rqdata", {"requestId": "remove-rqdata"})
        return {"coverage": {"complete": False}, "facts": []}

    execution = Mock(side_effect=execute)
    monkeypatch.setattr("src.services.thesis_ledger_rqdata_read.read_rqdata_fund_event_isolated", execution)
    arguments = dict(
        admitted_credential_revision=admitted,
        read_credentials=lambda: store.provider_credential_snapshot("rqdata"),
        read_master_key=lambda: ("synthetic-v1", b"synthetic-admission-key"), timeout_seconds=4,
        query_fund_code="000246", instrument_type="NAV_FUND", start="2025-01-01", end="2025-12-31",
    )
    if change == "none":
        result = read_rqdata_fund_event_with_credentials("split", "000246.OF", **arguments)
        assert result == {"coverage": {"complete": False}, "facts": []}
        assert_redacted(result)
    else:
        with pytest.raises(ValueError, match="^rqdata_credential_not_admitted$"):
            read_rqdata_fund_event_with_credentials("split", "000246.OF", **arguments)
        assert revision(store.provider_credential_snapshot("rqdata")) != admitted
    assert execution.call_count == 1
