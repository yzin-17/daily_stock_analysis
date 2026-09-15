import json

import pytest

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_oauth_contract import OAuthStateError
from src.services.provider_oauth_runtime import build_page_oauth
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _encrypt_secret


def test_runtime_uses_snapshot_and_versioned_refresh_without_browser(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "oauth-runtime-fixture-key")
    control = ThesisLedgerControlStore(str(tmp_path / "oauth.db"))
    repository = ProviderOAuthStore(control.database_path, encrypt=_encrypt_secret)
    token = json.dumps(
        {
            "client_id": "client",
            "access_token": "fixture",
            "refresh_token": "fixture-refresh",
            "expires_at": 1900000000,
        }
    )
    session, _ = repository.create("client")
    version = repository.save_authorized_token(session["sessionId"], token)
    snapshot = ProviderCredentialSnapshot.create(
        "longbridge", "control", "oauth", {"clientId": "client", "tokenJson": token}, 1, version
    )

    class Builder:
        def __init__(self, client_id, **kwargs):
            assert client_id == "client"
            assert kwargs["token_json"] == token
            assert kwargs["allow_authorization"] is False
            self.save = kwargs["on_token_save"]

        def build(self, open_url):
            return self

    first = build_page_oauth(snapshot, control.database_path, builder_factory=lambda: Builder)
    second = build_page_oauth(snapshot, control.database_path, builder_factory=lambda: Builder)
    first.save(token)
    with pytest.raises(OAuthStateError, match="CONFIG_CHANGED"):
        second.save(token)
    first.save(token)
    control.save_provider_config("longbridge", {"clearCredentials": True})
    with pytest.raises(OAuthStateError, match="CONFIG_CHANGED"):
        first.save(token)


def test_runtime_failure_does_not_echo_sdk_token(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "oauth-runtime-fixture-key")
    control = ThesisLedgerControlStore(str(tmp_path / "oauth.db"))
    token = json.dumps({"client_id": "client", "access_token": "fixture", "expires_at": 1900000000})
    snapshot = ProviderCredentialSnapshot.create(
        "longbridge", "control", "oauth", {"clientId": "client", "tokenJson": token}, 1, 1
    )

    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        def build(self, open_url):
            raise RuntimeError("expired token=fixture-secret")

    with pytest.raises(OAuthStateError, match="OAUTH_REAUTH_REQUIRED") as caught:
        build_page_oauth(snapshot, control.database_path, builder_factory=lambda: Builder)
    assert "fixture-secret" not in str(caught.value)
