import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.services.provider_oauth_store import OAuthStateError, ProviderOAuthStore
from src.services.thesis_ledger_control import (
    ThesisLedgerControlStore,
    _decrypt_secret,
    _encrypt_secret,
)


@pytest.fixture
def stores(monkeypatch, tmp_path):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "oauth-test-key-never-used-in-production")
    control = ThesisLedgerControlStore(str(tmp_path / "oauth.db"))
    now = [1800000000]
    oauth = ProviderOAuthStore(control.database_path, encrypt=_encrypt_secret, clock=lambda: now[0])
    return control, oauth, now


def token(access="fixture-access", client="client"):
    return json.dumps(
        {
            "client_id": client,
            "access_token": access,
            "refresh_token": "fixture-refresh",
            "expires_at": 1900000000,
        }
    )


def row(control):
    with sqlite3.connect(control.database_path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM thesis_ledger_provider_config WHERE provider_id='longbridge'"
        ).fetchone()


def test_authorization_publishes_encrypted_token_and_terminal_atomically(stores):
    control, oauth, _ = stores
    control.save_provider_config("longbridge", {"enabled": False, "settings": {"keep": 1}})
    session, created = oauth.create("client")
    assert created
    assert oauth.set_authorization_url(
        session["sessionId"], "https://openapi.longbridge.com/oauth2/authorize?state=fixture"
    )
    assert oauth.get(session["sessionId"])["status"] == "authorizing"
    version = oauth.save_authorized_token(session["sessionId"], token())
    assert version == 1
    public = oauth.get(session["sessionId"])
    assert public["status"] == "succeeded"
    assert public["authorizationUrl"] is None
    assert "fixture-access" not in json.dumps(public)
    stored = row(control)
    assert stored["enabled"] == 0
    assert json.loads(stored["settings_json"]) == {"keep": 1}
    assert "fixture-access" not in stored["credential_ciphertext"]
    decrypted = json.loads(
        _decrypt_secret(stored["secret_key_version"], stored["credential_ciphertext"])
    )
    assert decrypted["method"] == "oauth"
    assert json.loads(decrypted["values"]["tokenJson"])["access_token"] == "fixture-access"
    with pytest.raises(OAuthStateError, match="INACTIVE"):
        oauth.save_authorized_token(session["sessionId"], token("replay"))


@pytest.mark.parametrize("action", ["cancel", "expire", "clear", "remove", "replace"])
def test_late_authorization_cannot_override_later_intent(stores, action):
    control, oauth, now = stores
    session, _ = oauth.create("client")
    if action == "cancel":
        oauth.finish(session["sessionId"], "cancelled")
    elif action == "expire":
        now[0] += 601
    elif action == "clear":
        control.save_provider_config("longbridge", {"clearCredentials": True})
    elif action == "remove":
        control.remove_provider("longbridge", {})
    else:
        control.save_provider_config(
            "longbridge",
            {
                "credentials": {
                    "method": "legacy",
                    "values": {"appKey": "a", "appSecret": "b", "accessToken": "c"},
                }
            },
        )
    before = row(control)
    with pytest.raises(OAuthStateError):
        oauth.save_authorized_token(session["sessionId"], token())
    after = row(control)
    before_values = tuple(before) if before else None
    after_values = tuple(after) if after else None
    assert before_values == after_values
    if action == "expire":
        assert oauth.get(session["sessionId"])["status"] == "expired"


def test_refresh_is_versioned_and_rejects_stale_instance(stores):
    control, oauth, _ = stores
    session, _ = oauth.create("client")
    first_version = oauth.save_authorized_token(session["sessionId"], token())
    next_version = oauth.save_refreshed_token("client", token("renewed"), first_version)
    assert next_version == first_version + 1
    with pytest.raises(OAuthStateError, match="CONFIG_CHANGED"):
        oauth.save_refreshed_token("client", token("old-request"), first_version)
    control.save_provider_config("longbridge", {"clearCredentials": True})
    with pytest.raises(OAuthStateError, match="CONFIG_CHANGED"):
        oauth.save_refreshed_token("client", token("after-clear"), next_version)
    assert row(control)["credential_ciphertext"] is None


def test_single_pending_session_and_restart_recovery(stores):
    _, oauth, _ = stores
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: oauth.create("client"), range(2)))
    assert sum(created for _, created in results) == 1
    assert results[0][0]["sessionId"] == results[1][0]["sessionId"]
    with pytest.raises(OAuthStateError, match="ALREADY_PENDING"):
        oauth.create("another-client")
    assert oauth.recover_pending() == 1
    assert oauth.current() is None
    previous = oauth.get(results[0][0]["sessionId"])
    assert previous["status"] == "failed"
    assert previous["errorCode"] == "OAUTH_RESTARTED"


def test_storage_error_rolls_back_both_credential_and_success(stores):
    control, oauth, _ = stores
    session, _ = oauth.create("client")

    def fail(_):
        raise RuntimeError("storage unavailable")

    oauth.encrypt = fail
    with pytest.raises(RuntimeError):
        oauth.save_authorized_token(session["sessionId"], token())
    assert row(control) is None
    assert oauth.get(session["sessionId"])["status"] == "starting"


def test_invalid_token_and_external_authorization_url_are_rejected(stores):
    _, oauth, _ = stores
    session, _ = oauth.create("client")
    with pytest.raises(OAuthStateError, match="TOKEN_INVALID"):
        oauth.save_authorized_token(session["sessionId"], token(client="wrong-client"))
    with pytest.raises(OAuthStateError, match="URL_INVALID"):
        oauth.set_authorization_url(session["sessionId"], "https://example.com/oauth2/authorize")
    assert oauth.get(session["sessionId"])["authorizationUrl"] is None
