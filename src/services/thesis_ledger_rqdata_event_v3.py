"""RQData 精确事件的当前账号准入与生产读取编排。"""

from datetime import datetime, timezone

from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot
from src.services.thesis_ledger_event_v3_adapters import RQDATA_REVISIONS, RQDATA_TARGET
from src.services.thesis_ledger_control import (
    ControlContractError, _provider_credential_revision_from_snapshot, _secret_key,
)
from src.services.thesis_ledger_market_v3_revisions import _market_v3_admission_matches_current
from src.services.thesis_ledger_rqdata_mapped_read import read_rqdata_mapped_fund_event

RQDATA_EVENT_TIMEOUT_SECONDS = 30


def current_rqdata_event_admission(store, key, target, manifest):
    """先核对准入与适配修订，缺准入不解密账号；账号轮换不能使用旧准入。"""
    if target != RQDATA_TARGET or key.get("capability") not in RQDATA_REVISIONS:
        return None
    try:
        admission = store.get_route_admission_v3(key=dict(key), target=dict(target))
        if not isinstance(admission, dict):
            return None
        event_admission_snapshot(admission, observed_at=datetime.now(timezone.utc))
        if any(admission.get(field) != value for field, value in RQDATA_REVISIONS[key["capability"]].items()):
            return None
        revision = _provider_credential_revision_from_snapshot(store.provider_credential_snapshot("rqdata"))
        if revision and _market_v3_admission_matches_current(
            admission, key, target, manifest, credential_revision=revision,
        ):
            return admission
    except (AttributeError, ControlContractError, TypeError, ValueError):
        return None
    return None


def read_rqdata_events_v3(runtime, request):
    key = request["routeKey"]
    return read_rqdata_mapped_fund_event(
        "split" if key["capability"] == "SPLIT_EVENT" else "dividend", request["symbol"],
        database_path=runtime.store.database_path, start=request["start"], end=request["end"],
        data_as_of=request["dataAsOf"],
        read_admission=lambda: runtime._current_market_v3_admission(key, RQDATA_TARGET),
        read_credentials=lambda: runtime.store.provider_credential_snapshot("rqdata"),
        read_master_key=_secret_key, timeout_seconds=RQDATA_EVENT_TIMEOUT_SECONDS,
    )
