"""Longbridge 环境凭证的只读解析；client ID 本身不构成认证凭证。"""

import base64
import os
import re
from src.services.provider_oauth_contract import validate_oauth_token


def longbridge_environment_values(config):
    from data_provider.longbridge_fetcher import _longbridge_credentials, _oauth_token_cache_path
    values = _longbridge_credentials(config)
    client_id = values.get("oauth_client_id")
    if client_id and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", client_id):
        try:
            encoded = os.environ.get("LONGBRIDGE_OAUTH_TOKEN_CACHE_B64", "").strip()
            if encoded:
                if len(encoded) > 96 * 1024:
                    raise ValueError("token size")
                token = base64.b64decode(encoded, validate=True).decode("utf-8")
            else:
                path = _oauth_token_cache_path(client_id)
                if path.stat().st_size > 64 * 1024:
                    raise ValueError("token size")
                token = path.read_text(encoding="utf-8")
            validate_oauth_token(client_id, token)
            return {"clientId": client_id, "tokenJson": token}
        except Exception:
            pass
    return {field: str(value).strip() for field, value in {
        "appKey": values.get("app_key"), "appSecret": values.get("app_secret"),
        "accessToken": values.get("access_token"),
    }.items() if value}
