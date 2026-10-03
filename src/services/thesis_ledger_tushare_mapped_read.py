"""Tushare 分红内部映射读取；库存及公共 wire 由独立后继接入。"""

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import math
from pathlib import Path
import re
import time

from data_provider.tushare_fund_dividend_reader import TushareFundDividendsMixin
from src.services.provider_credential_revision import provider_credential_revision
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.tushare_fund_identity_evidence import TushareFundIdentity, resolve_tushare_fund_identity


# 本地适配与源请求协议修订，不是上游数据历史版本；后续库存单向消费。
TUSHARE_FUND_DIV_ADAPTER_REVISION = "dsa-tushare-etf-fund-div-identity-v1"
TUSHARE_FUND_DIV_SOURCE_REVISION = "tushare-fund-div-symbol-history-decimal-v1"


@dataclass(frozen=True)
class TushareMappedFundDividends:
    result: dict
    identity: TushareFundIdentity


def _local_revision_check(admission):
    if (not isinstance(admission, dict)
            or admission.get("adapterRevision") != TUSHARE_FUND_DIV_ADAPTER_REVISION
            or admission.get("sourceRevision") != TUSHARE_FUND_DIV_SOURCE_REVISION
            or not isinstance(admission.get("credentialRevision"), str)
            or re.fullmatch(r"hmac-sha256-v1:[a-f0-9]{64}", admission["credentialRevision"]) is None):
        raise ValueError("tushare_mapped_not_admitted")


def _checked_snapshot(read_credentials, read_master_key, admitted_revision):
    try:
        original = read_credentials()
        if (not isinstance(original, ProviderCredentialSnapshot) or original.provider_id != "tushare"
                or original.source != "environment" or original.method != "token"):
            raise ValueError()
        snapshot = ProviderCredentialSnapshot.create(
            original.provider_id, original.source, original.method, original.values,
            original.config_version, original.credential_version,
        )
        version, key = read_master_key()
        if not isinstance(version, str) or not version or not isinstance(key, bytes) or not key:
            raise ValueError()
        revision = provider_credential_revision(snapshot, version, key)
        if revision is None or not hmac.compare_digest(revision, admitted_revision):
            raise ValueError()
        return snapshot
    except Exception:
        raise ValueError("tushare_mapped_credential_not_admitted") from None


def _validate_result(result, identity, start, end):
    if (not isinstance(result, dict) or result.get("coverage") != {"start": start, "end": end, "complete": False}
            or result["coverage"]["complete"] is not False or not isinstance(result.get("retrieval"), dict)):
        raise ValueError("tushare_mapped_invalid_result")
    retrieval = result["retrieval"]
    fingerprint = retrieval.get("contentFingerprint")
    if (retrieval.get("endpoint") != "fund_div" or retrieval.get("queryKind") != "symbol-history"
            or type(retrieval.get("requestCount")) is not int or retrieval["requestCount"] != 1
            or retrieval.get("upstreamPaginationVerified") is not False
            or retrieval.get("upstreamDataRevision") is not None
            or not isinstance(fingerprint, str) or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None
            or result.get("providerRevision") != f"tushare-fund-div-content-v1:{fingerprint}"
            or not isinstance(result.get("facts"), list) or not isinstance(result.get("observations"), list)):
        raise ValueError("tushare_mapped_invalid_result")
    if any(not isinstance(fact, dict) or fact.get("symbol") != identity.query_fund_code
           or fact.get("currency") != identity.currency or fact.get("instrumentType") != "ETF"
           for fact in result["facts"]):
        raise ValueError("tushare_mapped_invalid_result")


def read_tushare_mapped_fund_dividends(
    symbol, *, database_path, start, end, data_as_of, read_admission,
    read_credentials, read_master_key, build_fetcher, timeout_seconds,
):
    """复用可信快照构造接缝与精确 Reader，缺证据不读取账号或来源。"""
    if (isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("tushare_mapped_invalid_timeout")
    if not isinstance(database_path, str) or not database_path or database_path == ":memory:":
        raise ValueError("tushare_mapped_requires_persistent_store")
    deadline = time.monotonic() + timeout_seconds

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise TimeoutError("tushare_mapped_timeout")
        return budget

    evidence_store = MappingEvidenceStore(
        Path(database_path).expanduser().resolve().parent / "thesis-ledger-mapping-evidence",
    )
    before = deepcopy(read_admission())
    _local_revision_check(before)
    content = evidence_store.read(before.get("evidenceRef"))
    identity = resolve_tushare_fund_identity(
        content, before, symbol=symbol, start=start, end=end, data_as_of=data_as_of,
        observed_at=datetime.now(timezone.utc),
    )
    remaining()
    snapshot = _checked_snapshot(read_credentials, read_master_key, before["credentialRevision"])
    remaining()
    try:
        fetcher = build_fetcher(snapshot)
    except Exception:
        raise ValueError("tushare_mapped_adapter_unavailable") from None
    remaining()
    if (not isinstance(fetcher, TushareFundDividendsMixin)
            or getattr(fetcher, "_token", None) != snapshot.values["token"].strip()
            or getattr(fetcher, "_http_url", None) != snapshot.values["httpUrl"]):
        raise ValueError("tushare_mapped_adapter_snapshot_mismatch")
    try:
        result = fetcher.get_fund_dividends_for_source(
            identity.query_fund_code, "tushare", instrument_type="ETF", currency=identity.currency,
            start_date=start, end_date=end, timeout_seconds=remaining(),
        )
    except TimeoutError:
        raise TimeoutError("tushare_mapped_timeout") from None
    except Exception:
        raise ValueError("tushare_mapped_source_unavailable") from None
    remaining()
    after = deepcopy(read_admission())
    _local_revision_check(after)
    if after != before or evidence_store.read(identity.evidence_ref) != content:
        raise ValueError("tushare_mapped_admission_changed")
    resolve_tushare_fund_identity(
        content, after, symbol=symbol, start=start, end=end, data_as_of=data_as_of,
        observed_at=datetime.now(timezone.utc),
    )
    if _checked_snapshot(read_credentials, read_master_key, before["credentialRevision"]) != snapshot:
        raise ValueError("tushare_mapped_credential_changed")
    remaining()
    _validate_result(result, identity, start, end)
    return TushareMappedFundDividends(result=result, identity=identity)
