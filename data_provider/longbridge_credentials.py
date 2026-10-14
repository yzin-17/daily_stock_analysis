"""Longbridge 认证输入解析；不请求 SDK 或读写授权状态。"""

import os
from typing import Any, Dict, Optional

def _clean_optional(value):
    if value is None:
        return None
    return str(value).strip() or None

def _longbridge_credentials(config: Any = None) -> Dict[str, Optional[str]]:
    """Collect Longbridge auth inputs from Config/env without exposing secrets."""
    app_key = _clean_optional(getattr(config, "longbridge_app_key", None))
    app_secret = _clean_optional(getattr(config, "longbridge_app_secret", None))
    access_token = _clean_optional(getattr(config, "longbridge_access_token", None))
    oauth_client_id = _clean_optional(getattr(config, "longbridge_oauth_client_id", None))

    app_key = app_key or _clean_optional(os.getenv("LONGBRIDGE_APP_KEY"))
    app_secret = app_secret or _clean_optional(os.getenv("LONGBRIDGE_APP_SECRET"))
    access_token = access_token or _clean_optional(os.getenv("LONGBRIDGE_ACCESS_TOKEN"))
    oauth_client_id = oauth_client_id or _clean_optional(os.getenv("LONGBRIDGE_OAUTH_CLIENT_ID"))

    # Some Longbridge pages label the OAuth public client id as "App Key".
    # Use it as a compatibility alias only when no Legacy access token exists.
    if not oauth_client_id and app_key and not access_token:
        oauth_client_id = app_key

    return {
        "app_key": app_key,
        "app_secret": app_secret,
        "access_token": access_token,
        "oauth_client_id": oauth_client_id,
    }


def _has_legacy_credentials(creds: Dict[str, Optional[str]]) -> bool:
    return bool(creds.get("app_key") and creds.get("app_secret") and creds.get("access_token"))


def _has_oauth_credentials(creds: Dict[str, Optional[str]]) -> bool:
    return bool(creds.get("oauth_client_id"))


