"""真实定制 Python 绑定与 DSA 加密快照的离线接线验证。"""

import json

import pytest

from src.services.provider_oauth_runtime import build_page_oauth
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.thesis_ledger_control import ThesisLedgerControlStore, _encrypt_secret


def test_native_builder_loads_encrypted_snapshot_without_authorization(monkeypatch, tmp_path):
    sdk = pytest.importorskip("longbridge.openapi")
    if getattr(getattr(sdk, "OAuthBuilder", None), "THESIS_LEDGER_STORAGE_VERSION", None) != 1:
        pytest.skip("此验证必须在安装定制 SDK wheel 的环境执行")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "native-binding-fixture-key")
    store = ThesisLedgerControlStore(str(tmp_path / "native.db"))
    oauth = ProviderOAuthStore(store.database_path, encrypt=_encrypt_secret)
    session, _ = oauth.create("native-client")
    oauth.save_authorized_token(
        session["sessionId"],
        json.dumps(
            {
                "client_id": "native-client",
                "access_token": "native-fixture-access",
                "refresh_token": None,
                "expires_at": 2100000000,
            }
        ),
    )
    snapshot = store.provider_credential_snapshot("longbridge")
    value = build_page_oauth(snapshot, store.database_path)
    assert isinstance(value, sdk.OAuth)
    assert (
        store.provider_credential_snapshot("longbridge").credential_version
        == snapshot.credential_version
    )
    assert b"native-fixture-access" not in (tmp_path / "native.db").read_bytes()
