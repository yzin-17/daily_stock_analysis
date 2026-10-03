"""内部准入凭据指纹；用途隔离且不暴露原始 Token 或服务地址。"""

import hashlib
import hmac
import json

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot


def provider_credential_revision(snapshot, master_version, master_key):
    if not isinstance(snapshot, ProviderCredentialSnapshot):
        return None
    if snapshot.source != "environment" and not (snapshot.provider_id == "rqdata" and snapshot.source == "control"):
        return None
    if snapshot.provider_id == "hithink" and snapshot.method == "api_key":
        secret = snapshot.values.get("apiKey")
        if not isinstance(secret, str) or not secret.strip():
            return None
        payload = b"provider=hithink\0credential=api_key\0" + secret.strip().encode("utf-8")
    elif snapshot.provider_id == "tushare" and snapshot.method == "token":
        token, endpoint = snapshot.values.get("token"), snapshot.values.get("httpUrl")
        if (
            not isinstance(token, str) or not token.strip() or not isinstance(endpoint, str)
            or not endpoint.startswith(("http://", "https://"))
        ):
            return None
        payload = b"provider=tushare\0credential=token-and-endpoint-v1\0" + json.dumps(
            [token.strip(), endpoint], ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
    elif snapshot.provider_id == "rqdata" and snapshot.method == "username_password":
        username, password = snapshot.values.get("username"), snapshot.values.get("password")
        if (not isinstance(username, str) or not username.strip() or username != username.strip()
                or not isinstance(password, str) or not password.strip()):
            return None
        payload = b"provider=rqdata\0credential=username-password-v1\0" + json.dumps(
            [username, password], ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
        if snapshot.source == "control":
            payload = b"source=control\0" + payload
    else:
        return None
    context = b"thesis-ledger/provider-credential-revision/v1\0" + master_version.encode("utf-8")
    revision_key = hmac.new(master_key, context, hashlib.sha256).digest()
    return "hmac-sha256-v1:" + hmac.new(revision_key, payload, hashlib.sha256).hexdigest()
