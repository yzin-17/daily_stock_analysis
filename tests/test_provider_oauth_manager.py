import asyncio
import json

from src.services.provider_oauth_manager import ProviderOAuthManager
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _encrypt_secret


def setup_manager(monkeypatch, tmp_path, builder):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "oauth-manager-fixture-key")
    control = ThesisLedgerControlStore(str(tmp_path / "oauth.db"))
    repository = ProviderOAuthStore(control.database_path, encrypt=_encrypt_secret)
    manager = ProviderOAuthManager(repository, builder_factory=lambda: builder)
    manager.recover()
    return control, repository, manager


def test_authorization_callback_saves_and_query_has_no_secrets(monkeypatch, tmp_path):
    class Builder:
        def __init__(self, client_id, **kwargs):
            self.save = kwargs["on_token_save"]
            assert kwargs["allow_authorization"] is True
            assert kwargs["callback_port"] == 60355

        async def build_async(self, open_url):
            open_url("https://openapi.longbridge.com/oauth2/authorize?state=fixture")
            self.save(
                json.dumps(
                    {
                        "client_id": "client",
                        "access_token": "fixture-secret",
                        "refresh_token": None,
                        "expires_at": 1900000000,
                    }
                )
            )

    _, _, manager = setup_manager(monkeypatch, tmp_path, Builder)

    async def run():
        session = await manager.create("client")
        await asyncio.gather(*list(manager.tasks.values()))
        result = manager.get(session["sessionId"])
        assert result["status"] == "succeeded"
        assert "fixture-secret" not in json.dumps(result)

    asyncio.run(run())


def test_cancel_releases_executor_and_preserves_terminal(monkeypatch, tmp_path):
    events = []

    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        async def build_async(self, open_url):
            open_url("https://openapi.longbridge.com/oauth2/authorize")
            try:
                await asyncio.Future()
            finally:
                events.append("released")

    _, _, manager = setup_manager(monkeypatch, tmp_path, Builder)

    async def run():
        session = await manager.create("client")
        while manager.get(session["sessionId"])["status"] == "starting":
            await asyncio.sleep(0)
        result = await manager.cancel(session["sessionId"])
        assert result["status"] == "cancelled"
        assert events == ["released"]
        assert (await manager.cancel(session["sessionId"]))["status"] == "cancelled"

    asyncio.run(run())


def test_errors_are_terminal_and_redacted(monkeypatch, tmp_path):
    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        async def build_async(self, open_url):
            raise OSError("Address already in use; fixture-secret")

    _, _, manager = setup_manager(monkeypatch, tmp_path, Builder)

    async def run():
        session = await manager.create("client")
        await asyncio.gather(*list(manager.tasks.values()))
        result = manager.get(session["sessionId"])
        assert result["status"] == "failed"
        assert result["errorCode"] == "OAUTH_PORT_IN_USE"
        assert "fixture-secret" not in json.dumps(result)

    asyncio.run(run())


def test_missing_save_never_claims_success(monkeypatch, tmp_path):
    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        async def build_async(self, open_url):
            pass

    _, _, manager = setup_manager(monkeypatch, tmp_path, Builder)

    async def run():
        session = await manager.create("client")
        await asyncio.gather(*list(manager.tasks.values()))
        assert manager.get(session["sessionId"])["errorCode"] == "OAUTH_TOKEN_NOT_SAVED"

    asyncio.run(run())


def test_shutdown_releases_listener_even_when_storage_fails(monkeypatch, tmp_path, caplog):
    released = []

    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        async def build_async(self, open_url):
            open_url("https://openapi.longbridge.com/oauth2/authorize")
            try:
                await asyncio.Future()
            finally:
                released.append(True)

    _, repository, manager = setup_manager(monkeypatch, tmp_path, Builder)

    async def run():
        session = await manager.create("client")
        while manager.get(session["sessionId"])["status"] == "starting":
            await asyncio.sleep(0)

        def fail(*args, **kwargs):
            raise RuntimeError("storage-fixture-secret")

        monkeypatch.setattr(repository, "finish", fail)
        await manager.shutdown()
        assert released == [True]
        assert not manager.tasks

    asyncio.run(run())
    assert "storage-fixture-secret" not in caplog.text
