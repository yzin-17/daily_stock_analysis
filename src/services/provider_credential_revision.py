"""内部准入凭据指纹；用途隔离且不暴露原始 Token 或服务地址。"""

import hashlib
import hmac

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot


def provider_credential_revision(snapshot, master_version, master_key):
    if not isinstance(snapshot, ProviderCredentialSnapshot):
        return None
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES

    source = CAPABILITY_SOURCES.source(snapshot.provider_id)
    if source is None or source.credential_revision_payload is None:
        return None
    payload = source.credential_revision_payload(snapshot)
    if not isinstance(payload, bytes) or not payload:
        return None
    context = b"thesis-ledger/provider-credential-revision/v1\0" + master_version.encode("utf-8")
    revision_key = hmac.new(master_key, context, hashlib.sha256).digest()
    return "hmac-sha256-v1:" + hmac.new(revision_key, payload, hashlib.sha256).hexdigest()
