"""HiThink ETF 分红的身份先行、单快照读取；生产库存由独立入口开放。"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import math
from pathlib import Path
import re
import time

from data_provider.hithink_fund_dividends import normalize_hithink_fund_dividends
from data_provider.hithink_fund_dividend_reader import fetch_hithink_fund_dividends
from src.services.hithink_fund_identity_evidence import (
    HiThinkFundIdentity, _instant, resolve_hithink_fund_identity,
)
from src.services.provider_credential_revision import provider_credential_revision
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_hithink_dividend_contract_v3 import (
    HITHINK_DIVIDEND_ADAPTER_REVISION, HITHINK_DIVIDEND_SOURCE_REVISION,
)
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore


@dataclass(frozen=True)
class HiThinkMappedFundDividends:
    result: dict
    identity: HiThinkFundIdentity
    response_sha256: str


def _local_revision_check(admission):
    if (not isinstance(admission, dict) or admission.get("admissionState") != "admitted"
            or admission.get("adapterRevision") != HITHINK_DIVIDEND_ADAPTER_REVISION
            or admission.get("sourceRevision") != HITHINK_DIVIDEND_SOURCE_REVISION
            or not isinstance(admission.get("credentialRevision"), str)
            or re.fullmatch(r"hmac-sha256-v1:[a-f0-9]{64}", admission["credentialRevision"]) is None):
        raise ValueError("hithink_mapped_not_admitted")


def _checked_snapshot(read_credentials, read_master_key, admitted_revision):
    try:
        original = read_credentials()
        if (not isinstance(original, ProviderCredentialSnapshot) or original.provider_id != "hithink"
                or original.source != "environment" or original.method != "api_key"):
            raise ValueError()
        snapshot = ProviderCredentialSnapshot.create(
            original.provider_id, original.source, original.method, original.values,
            original.config_version, original.credential_version,
        )
        if (set(snapshot.values) != {"apiKey"} or not isinstance(snapshot.values["apiKey"], str)
                or not snapshot.values["apiKey"].strip()):
            raise ValueError()
        version, key = read_master_key()
        if not isinstance(version, str) or not version or not isinstance(key, bytes) or not key:
            raise ValueError()
        revision = provider_credential_revision(snapshot, version, key)
        if revision is None or not hmac.compare_digest(revision, admitted_revision):
            raise ValueError()
        return snapshot
    except Exception:
        raise ValueError("hithink_mapped_credential_not_admitted") from None


def read_hithink_mapped_fund_dividends(
    symbol, *, database_path, start, end, data_as_of, read_admission,
    read_credentials, read_master_key, fetch=fetch_hithink_fund_dividends,
    timeout_seconds=30,
):
    """读前审核原文、固定凭据，读后核对撤销及变更；不声明历史完整。"""
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 60):
        raise ValueError("hithink_mapped_invalid_timeout")
    if not isinstance(database_path, str) or not database_path or database_path == ":memory:":
        raise ValueError("hithink_mapped_requires_persistent_store")
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("hithink_mapped_timeout")
        return budget

    evidence_store = MappingEvidenceStore(
        Path(database_path).expanduser().resolve().parent / "thesis-ledger-mapping-evidence",
    )
    before = deepcopy(read_admission())
    _local_revision_check(before)
    content = evidence_store.read(before.get("evidenceRef"))
    identity = resolve_hithink_fund_identity(
        content, before, symbol=symbol, start=start, end=end, data_as_of=data_as_of,
        observed_at=datetime.now(timezone.utc),
    )
    remaining()
    snapshot = _checked_snapshot(read_credentials, read_master_key, before["credentialRevision"])
    remaining()
    try:
        raw = fetch(identity.query_thscode, fund_type="exchange",
                    api_key=snapshot.values["apiKey"], timeout_seconds=remaining())
    except TimeoutError:
        raise TimeoutError("hithink_mapped_timeout") from None
    except Exception:
        raise ValueError("hithink_mapped_source_unavailable") from None
    remaining()
    after = deepcopy(read_admission())
    _local_revision_check(after)
    if after != before or evidence_store.read(identity.evidence_ref) != content:
        raise ValueError("hithink_mapped_admission_changed")
    resolve_hithink_fund_identity(
        content, after, symbol=symbol, start=start, end=end, data_as_of=data_as_of,
        observed_at=datetime.now(timezone.utc),
    )
    if _checked_snapshot(read_credentials, read_master_key, before["credentialRevision"]) != snapshot:
        raise ValueError("hithink_mapped_credential_changed")
    remaining()
    fingerprint = raw.get("responseSha256") if isinstance(raw, dict) else None
    observed = raw.get("observedAt") if isinstance(raw, dict) else None
    if (not isinstance(raw, dict) or raw.get("symbol") != symbol or raw.get("fundType") != "exchange"
            or raw.get("historyComplete") is not False
            or not isinstance(raw.get("items"), list)
            or not isinstance(fingerprint, str) or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
            or not isinstance(observed, str)):
        raise ValueError("hithink_mapped_invalid_result")
    try:
        observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
        if (observed_at.tzinfo is None or _instant(observed) > _instant(data_as_of)
                or _instant(observed) > _instant(datetime.now(timezone.utc).isoformat())):
            raise ValueError()
        revision = f"hithink-fund-dividends-content-v1:{fingerprint}"
        result = normalize_hithink_fund_dividends(
            raw["items"], symbol, instrument_type="ETF", currency=identity.currency,
            start=start, end=end, observed_at=observed_at, provider_revision=revision,
        )
        if result["coverage"] != {"start": start, "end": end, "complete": False}:
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("hithink_mapped_invalid_result") from None
    result["providerRevision"] = revision
    return HiThinkMappedFundDividends(result, identity, fingerprint)
