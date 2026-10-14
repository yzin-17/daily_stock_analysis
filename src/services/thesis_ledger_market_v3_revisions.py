"""精确来源适配修订与准入有效性，供目录及数据执行共用。"""

from __future__ import annotations

import hmac
from typing import Any, Mapping

from src.services.thesis_ledger_market_v3_adapters import market_v3_bar_adapter_reason
from src.services.thesis_ledger_capability_registry import DailyBinding
from src.services.thesis_ledger_route_admission_v3 import route_admission_scope_applies
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from src.services.thesis_ledger_event_v3_adapters import event_adapter_revisions, event_adapter_matches
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES

MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION = "not-required"

# These revisions describe the local exact adapter implementation. Bump the
# affected entry whenever its source dispatch or adjustment behavior changes.
_MARKET_V3_ADAPTER_REVISIONS = {
}


def market_v3_current_route_revisions(
    key: Mapping[str, Any],
    target: Mapping[str, Any],
    provider_manifest: Mapping[str, Any],
    *,
    credential_version: int | None = None,
    credential_revision: str | None = None,
) -> dict[str, str] | None:
    """Resolve current local revisions only for an exact executable adapter."""
    if not isinstance(key, Mapping) or not isinstance(target, Mapping):
        return None
    if not isinstance(provider_manifest, Mapping):
        return None
    if current_data_adapter_revisions(key, target) is not None:
        if provider_manifest.get("providerId") != target.get("providerId"):
            return None
        if provider_manifest.get("requiresCredential") is not False:
            return None
        return current_data_adapter_revisions(key, target)
    if event_adapter_matches(key, target):
        provider_id = target["providerId"]
        source = CAPABILITY_SOURCES.source(provider_id)
        if source is None:
            return None
        requires_credential = source.manifest().get('requiresCredential')
        if (provider_manifest.get("providerId") != provider_id
                or provider_manifest.get("requiresCredential") is not requires_credential):
            return None
        return event_adapter_revisions(key, target, credential_revision)
    if market_v3_bar_adapter_reason(dict(key), dict(target)) is not None:
        return None

    provider_id = str(target.get("providerId") or "").strip().lower()
    if provider_manifest.get("providerId") != provider_id:
        return None
    binding = CAPABILITY_SOURCES.resolve(key, target)
    if not isinstance(binding, DailyBinding):
        return None
    adapter_revision, source_revision = binding.adapter_revision, binding.source_revision
    if (
        not adapter_revision
        or not isinstance(binding.protocol, str)
        or not binding.protocol.strip()
        or not isinstance(source_revision, str)
        or not source_revision.strip()
    ):
        return None

    requires_credential = provider_manifest.get("requiresCredential")
    if requires_credential is False:
        credential_revision = MARKET_V3_CREDENTIAL_NOT_REQUIRED_REVISION
    elif requires_credential is True:
        source = CAPABILITY_SOURCES.source(provider_id)
        if source is None:
            return None
        if source.credential_revision_kind == 'hmac':
            if (
                not isinstance(credential_revision, str)
                or not credential_revision.startswith("hmac-sha256-v1:")
                or len(credential_revision.removeprefix("hmac-sha256-v1:")) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in credential_revision.removeprefix("hmac-sha256-v1:")
                )
            ):
                return None
        else:
            if (
                not isinstance(credential_version, int)
                or isinstance(credential_version, bool)
                or credential_version < 0
            ):
                return None
            credential_revision = str(credential_version)
    else:
        return None

    return {
        "adapterRevision": adapter_revision,
        "sourceRevision": source_revision.strip(),
        "credentialRevision": credential_revision,
    }


def _market_v3_admission_matches_current(
    admission: Mapping[str, Any],
    key: Mapping[str, Any],
    target: Mapping[str, Any],
    provider_manifest: Mapping[str, Any],
    *,
    credential_version: int | None = None,
    credential_revision: str | None = None,
) -> bool:
    if admission.get("admissionState") != "admitted":
        return False
    if admission.get("consumer") != "thesis-ledger":
        return False

    current_revisions = market_v3_current_route_revisions(
        key,
        target,
        provider_manifest,
        credential_version=credential_version,
        credential_revision=credential_revision,
    )
    if current_revisions is None:
        return False
    for revision_field, value in current_revisions.items():
        observed = admission.get(revision_field)
        if revision_field == "credentialRevision" and value.startswith("hmac-sha256-v1:"):
            if not isinstance(observed, str) or not hmac.compare_digest(observed, value):
                return False
        elif observed != value:
            return False

    symbols = admission.get("scopeSymbols")
    date_from = admission.get("scopeDateFrom")
    date_to = admission.get("scopeDateTo")
    if (
        not isinstance(symbols, (list, tuple))
        or not symbols
        or not isinstance(date_from, str)
        or not isinstance(date_to, str)
    ):
        return False
    return all(
        isinstance(symbol, str)
        and route_admission_scope_applies(
            dict(admission),
            symbol=symbol,
            date_from=date_from,
            date_to=date_to,
        )
        for symbol in symbols
    )
