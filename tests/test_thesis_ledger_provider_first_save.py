"""首次凭据保存沿用原启停状态，只使用合成凭据与临时 SQLite。"""

from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from src.services import thesis_ledger_control as control
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


@pytest.fixture
def client_and_store(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "first-save.db"))
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "synthetic-control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "synthetic-encryption-key")
    monkeypatch.setattr(control, "_environment_credential_values", lambda _: {})
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    with TestClient(app) as client:
        yield client, control.ThesisLedgerControlStore()


def provider(store, provider_id):
    return next(item for item in store.provider_registry() if item["providerId"] == provider_id)


def save(client, provider_id, **values):
    return client.post(
        f"/api/v3/thesis-ledger/control/providers/{provider_id}/config",
        headers={"authorization": "Bearer synthetic-control-token"},
        json={
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "requestId": "first-credential-save",
            "expectedConfigVersion": 0,
            **values,
        },
    )


@pytest.mark.parametrize("provider_id,method,field", [
    ("tickflow", "api_key", "apiKey"),
    ("finnhub", "api_key", "apiKey"),
    ("alphavantage", "api_key", "apiKey"),
    ("tushare", "token", "token"),
])
def test_first_credentials_keep_unconfigured_provider_disabled(client_and_store, provider_id, method, field):
    client, store = client_and_store
    before = provider(store, provider_id)
    assert before["enabled"] is False
    assert before["configVersion"] == 0
    response = save(
        client, provider_id,
        credentials={"method": method, "values": {field: "synthetic-page-secret"}},
        settings={"source": "first-save"},
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    after = provider(store, provider_id)
    assert after["enabled"] is False
    assert after["credentialConfigured"] is True
    assert after["credentialSource"] == "control"
    assert after["configVersion"] == 1
    assert "synthetic-page-secret" not in response.text
    snapshot = store.provider_credential_snapshot(provider_id)
    assert snapshot.values[field] == "synthetic-page-secret"
    assert snapshot.credential_version == 1
    with store._connect() as connection:
        row = connection.execute(
            "SELECT enabled, settings_json FROM thesis_ledger_provider_config WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
    assert row["enabled"] == 0
    assert row["settings_json"] == '{"source":"first-save"}'


@pytest.mark.parametrize("default_enabled", [False, True])
@pytest.mark.parametrize("environment_configured", [False, True])
def test_first_save_preserves_preexisting_environment_and_source_defaults(
    client_and_store, monkeypatch, default_enabled, environment_configured,
):
    client, store = client_and_store
    monkeypatch.setattr(CAPABILITY_SOURCES, "sources", tuple(
        replace(source, default_enabled=default_enabled) if source.provider_id == "tickflow" else source
        for source in CAPABILITY_SOURCES.sources
    ))
    monkeypatch.setattr(control, "_environment_credential_values", lambda provider_id: (
        {"apiKey": "synthetic-environment-secret"}
        if provider_id == "tickflow" and environment_configured else {}
    ))
    before = provider(store, "tickflow")
    assert before["enabled"] is (default_enabled and environment_configured)
    response = save(client, "tickflow", credentials={
        "method": "api_key", "values": {"apiKey": "synthetic-page-secret"},
    })
    assert response.status_code == 200
    assert response.json()["enabled"] is before["enabled"]
    assert provider(store, "tickflow")["enabled"] is before["enabled"]


@pytest.mark.parametrize("enabled", [False, True])
def test_first_save_honors_explicit_state_with_credentials(client_and_store, enabled):
    client, store = client_and_store
    response = save(client, "tickflow", enabled=enabled, credentials={
        "method": "api_key", "values": {"apiKey": "synthetic-page-secret"},
    })
    assert response.status_code == 200
    assert response.json()["enabled"] is enabled
    assert provider(store, "tickflow")["enabled"] is enabled


def test_first_empty_save_keeps_public_source_enabled(client_and_store):
    client, store = client_and_store
    assert provider(store, "efinance")["enabled"] is True
    response = save(client, "efinance")
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert provider(store, "efinance")["enabled"] is True


def test_empty_credential_patch_keeps_disabled_state_and_credential_revision(client_and_store):
    client, store = client_and_store
    initial = save(client, "tickflow", enabled=False, credentials={
        "method": "api_key", "values": {"apiKey": "synthetic-page-secret"},
    })
    assert initial.status_code == 200
    before = store.provider_credential_snapshot("tickflow")
    response = save(client, "tickflow", expectedConfigVersion=1, credentials={"method": "api_key", "values": {}})
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    after = store.provider_credential_snapshot("tickflow")
    assert after.values == before.values
    assert after.credential_version == before.credential_version
    assert after.config_version == before.config_version + 1
