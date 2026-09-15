"""页面 OAuth 令牌结构、会话状态与安全错误契约。"""

import json

PENDING = ("starting", "authorizing")
PROVIDER = "longbridge"


class OAuthStateError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def validate_oauth_token(client_id: str, token_json: str) -> dict:
    try:
        value = json.loads(token_json)
    except (ValueError, TypeError):
        raise OAuthStateError("OAUTH_TOKEN_INVALID") from None
    if not isinstance(value, dict) or set(value) - {
        "client_id",
        "access_token",
        "refresh_token",
        "expires_at",
    }:
        raise OAuthStateError("OAUTH_TOKEN_INVALID")
    if (
        value.get("client_id") != client_id
        or not isinstance(value.get("access_token"), str)
        or not value["access_token"]
    ):
        raise OAuthStateError("OAUTH_TOKEN_INVALID")
    expiry = value.get("expires_at")
    if not isinstance(expiry, int) or isinstance(expiry, bool) or expiry < 0:
        raise OAuthStateError("OAUTH_TOKEN_INVALID")
    refresh = value.get("refresh_token")
    if refresh is not None and (not isinstance(refresh, str) or not refresh):
        raise OAuthStateError("OAUTH_TOKEN_INVALID")
    return value
