"""从加密快照构建不启动浏览器的页面 OAuth 行情实例。"""

import threading

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.provider_oauth_manager import sdk_oauth_builder
from src.services.provider_oauth_store import ProviderOAuthStore
from src.services.provider_oauth_contract import OAuthStateError, validate_oauth_token
from src.services.thesis_ledger_control import _encrypt_secret


def build_page_oauth(
    snapshot: ProviderCredentialSnapshot, database_path: str, *, builder_factory=sdk_oauth_builder, read_only=False
):
    if (
        snapshot.provider_id != "longbridge"
        or snapshot.source not in {"control", "environment"}
        or snapshot.method != "oauth"
    ):
        raise OAuthStateError("OAUTH_CREDENTIAL_INVALID")
    client_id = snapshot.values.get("clientId", "")
    token_json = snapshot.values.get("tokenJson", "")
    validate_oauth_token(client_id, token_json)
    repository = None if read_only or snapshot.source == "environment" else ProviderOAuthStore(database_path, encrypt=_encrypt_secret)
    expected_version = [snapshot.credential_version]
    lock = threading.Lock()

    def save(token: str):
        if read_only or snapshot.source == "environment":
            validate_oauth_token(client_id, token)
            return
        with lock:
            assert repository is not None
            expected_version[0] = repository.save_refreshed_token(
                client_id, token, expected_version[0]
            )

    def refuse_authorization(_url):
        raise OAuthStateError("OAUTH_REAUTH_REQUIRED")

    builder = builder_factory()(
        client_id,
        token_json=token_json,
        on_token_save=save,
        allow_authorization=False,
    )
    try:
        return builder.build(refuse_authorization)
    except OAuthStateError:
        raise
    except Exception:
        raise OAuthStateError("OAUTH_REAUTH_REQUIRED") from None
