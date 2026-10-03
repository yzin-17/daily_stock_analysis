import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_oauth import router
from src.services.provider_oauth_manager import ProviderOAuthManager
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _encrypt_secret


def test_control_boundary_create_resume_and_cancel(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-fixture")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "encryption-fixture")
    control = ThesisLedgerControlStore(str(tmp_path / "api.db"))

    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        async def build_async(self, open_url):
            open_url("https://openapi.longbridge.com/oauth2/authorize?state=fixture")
            await asyncio.Future()

    manager = ProviderOAuthManager(
        ProviderOAuthStore(control.database_path, encrypt=_encrypt_secret),
        builder_factory=lambda: Builder,
    )
    app = FastAPI()
    app.state.provider_oauth_manager = manager
    app.include_router(router, prefix="/api/v3")
    base = "/api/v3/thesis-ledger/control/providers/longbridge/oauth/sessions"
    headers = {"Authorization": "Bearer control-fixture"}
    with TestClient(app) as client:
        assert client.get(base + "/current").status_code == 401
        assert client.get("/api/v1/thesis-ledger/control/providers/longbridge/oauth/sessions/current", headers=headers).status_code == 404
        legacy = client.post(
            base,
            json={"contractVersion": 1, "consumer": "thesis-ledger", "requestId": "old-oauth", "clientId": "client"},
            headers=headers,
        )
        assert legacy.status_code == 422
        assert legacy.json()["detail"]["code"] == "CONTROL_CONTRACT_UNSUPPORTED"
        response = client.post(
            base,
            json={"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "oauth-test", "clientId": "client"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        session_id = response.json()["sessionId"]
        assert response.json()["contractVersion"] == 3
        assert response.json()["consumer"] == "thesis-ledger"
        import sqlite3
        def snapshot():
            with sqlite3.connect(control.database_path) as connection:
                return tuple(connection.iterdump())
        before = snapshot()
        for version in (1, 2):
            cancelled_old = client.post(base + f"/{session_id}/cancel", headers=headers,
                json={"contractVersion": version, "consumer": "thesis-ledger", "requestId": "old-cancel"})
            assert cancelled_old.status_code == 422
            assert snapshot() == before
        resumed = client.get(base + "/current", headers=headers).json()["session"]
        assert resumed["sessionId"] == session_id
        assert resumed["status"] in {"starting", "authorizing"}
        cancelled = client.post(base + f"/{session_id}/cancel", headers=headers,
                                json={"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "cancel-current"})
        assert cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["authorizationUrl"] is None
        assert client.get(base + "/current", headers=headers).json() == {
            "contractVersion": 3, "consumer": "thesis-ledger", "session": None,
        }
        assert client.get(base + "/missing", headers=headers).status_code == 404
        assert (
            client.post(
                base,
                json={
                    "contractVersion": 3,
                    "consumer": "thesis-ledger",
                    "requestId": "oauth-invalid-test",
                    "clientId": "client",
                    "access_token": "never-accept",
                },
                headers=headers,
            ).status_code
            == 400
        )


def test_startup_recovers_pending_and_shutdown_removes_manager(monkeypatch, tmp_path):
    import api.thesis_ledger_oauth as oauth_api

    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-fixture")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "encryption-fixture")
    monkeypatch.setenv("THESIS_LEDGER_OAUTH_CALLBACK_HOST", "0.0.0.0")
    control = ThesisLedgerControlStore(str(tmp_path / "lifecycle.db"))
    repository = ProviderOAuthStore(control.database_path, encrypt=_encrypt_secret)
    pending, _ = repository.create("client")
    monkeypatch.setattr(oauth_api, "_control_store", lambda: control)
    app = FastAPI()
    oauth_api.initialize_provider_oauth(app)
    manager = app.state.provider_oauth_manager
    assert manager.callback_host == "0.0.0.0"
    assert manager.get(pending["sessionId"])["errorCode"] == "OAUTH_RESTARTED"
    asyncio.run(oauth_api.shutdown_provider_oauth(app))
    assert not hasattr(app.state, "provider_oauth_manager")
