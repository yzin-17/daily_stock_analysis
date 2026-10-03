"""RQData 事件读取的准入映射接缝；事件路由注册由上层另行交付。"""

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from src.services.rqdata_fund_identity_evidence import resolve_rqdata_fund_identity
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services.thesis_ledger_rqdata_read import read_rqdata_fund_event_with_credentials


def read_rqdata_mapped_fund_event(
    kind, symbol, *, database_path, start, end, data_as_of, read_admission,
    read_credentials, read_master_key, timeout_seconds, maximum_rows=2000,
):
    if not database_path or database_path == ":memory:":
        raise ValueError("rqdata_identity_requires_persistent_store")
    evidence_store = MappingEvidenceStore(
        Path(database_path).expanduser().resolve().parent / "thesis-ledger-mapping-evidence",
    )
    before = deepcopy(read_admission())
    if not isinstance(before, dict) or not isinstance(before.get("evidenceRef"), str):
        raise ValueError("rqdata_identity_not_admitted")
    content = evidence_store.read(before["evidenceRef"])
    identity = resolve_rqdata_fund_identity(
        content, before, kind=kind, symbol=symbol, start=start, end=end, data_as_of=data_as_of,
        observed_at=datetime.now(timezone.utc),
    )
    options = {"query_fund_code": identity.query_fund_code, "instrument_type": "ETF",
               "start": start, "end": end, "maximum_rows": maximum_rows}
    if kind == "dividend":
        options["currency"] = identity.currency
    result = read_rqdata_fund_event_with_credentials(
        kind, symbol, admitted_credential_revision=before["credentialRevision"],
        read_credentials=read_credentials, read_master_key=read_master_key,
        timeout_seconds=timeout_seconds, **options,
    )
    after = read_admission()
    if after != before or evidence_store.read(identity.evidence_ref) != content:
        raise ValueError("rqdata_identity_admission_changed")
    resolve_rqdata_fund_identity(
        content, after, kind=kind, symbol=symbol, start=start, end=end, data_as_of=data_as_of,
        observed_at=datetime.now(timezone.utc),
    )
    return {**result, "identityEvidence": {
        "ref": identity.evidence_ref, "sha256": identity.evidence_sha256, "content": content.decode("utf-8"),
    }}
