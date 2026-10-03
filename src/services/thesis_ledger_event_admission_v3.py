"""输出事件读取时已核验的准入快照，不暴露审核人或凭据内容。"""

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any


def event_admission_snapshot(admission: dict[str, Any], *, observed_at: datetime) -> dict[str, Any]:
    fields = (
        "consumer", "routeKey", "target", "status", "admissionState", "evidenceRef",
        "evidenceSha256", "scopeSymbols", "scopeDateFrom", "scopeDateTo", "adapterRevision",
        "sourceRevision", "credentialRevision", "validFrom", "validUntil", "recordVersion",
        "recordedAt", "invalidatedAt", "invalidationReason",
    )
    if any(field not in admission for field in fields):
        raise ValueError("invalid admission snapshot")
    if (admission["consumer"] != "thesis-ledger" or admission["status"] != "admitted"
            or admission["admissionState"] != "admitted"
            or admission["invalidatedAt"] is not None or admission["invalidationReason"] is not None
            or type(admission["recordVersion"]) is not int or admission["recordVersion"] < 1):
        raise ValueError("invalid admission state")
    for field in ("evidenceRef", "adapterRevision", "sourceRevision", "credentialRevision"):
        if not isinstance(admission[field], str) or not admission[field].strip():
            raise ValueError("invalid admission revision")
    digest = admission["evidenceSha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid admission evidence digest")
    times = []
    for field in ("validFrom", "validUntil", "recordedAt"):
        value = admission[field]
        if not isinstance(value, str):
            raise ValueError("invalid admission time")
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("invalid admission timezone")
        times.append(instant.astimezone(timezone.utc))
    starts, expires, recorded = times
    if not starts <= observed_at < expires or recorded > observed_at:
        raise ValueError("admission is not current at observation")
    return deepcopy({field: admission[field] for field in fields})
