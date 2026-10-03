from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS,
    _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_market_v3_adapters import (
    HITHINK_STOCK_HISTORY_SOURCE,
    HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1,
    HITHINK_STOCK_SOURCE_CONTRACT_REVISION,
    iter_market_v3_bar_adapters,
    iter_market_v3_gated_bar_adapters,
    market_v3_bar_adapter_reason,
)
from src.services.thesis_ledger_market_v3_facts import market_calendar_evidence_v3
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
    market_v3_current_route_revisions,
)
from src.services.thesis_ledger_route_admission_v3 import canonical_route_key_json


SYMBOL = "000001.SZ"
START = "2026-05-18"
END = "2026-05-20"
TEST_API_KEY = "synthetic-hithink-stock-key"
BASE_ROUTE_KEY = {
    "kind": "bar",
    "market": "CN",
    "assetType": "STOCK",
    "capability": "DAILY_BAR",
    "timeframe": "1d",
}


def _route_key(adjustment: str) -> dict[str, str]:
    return {**BASE_ROUTE_KEY, "adjustment": adjustment}


def _target(source: str = HITHINK_STOCK_HISTORY_SOURCE) -> dict[str, str]:
    return {"providerId": "hithink", "upstreamSource": source}


def _snapshot(api_key: str = TEST_API_KEY) -> ProviderCredentialSnapshot:
    return ProviderCredentialSnapshot.create(
        "hithink",
        "environment",
        "api_key",
        {"apiKey": api_key} if api_key else {},
        config_version=0,
        credential_version=0,
    )


def _set_synthetic_master(monkeypatch, value: str = "synthetic-dsa-master", version: str = "test-v1"):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", value)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", version)
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)


def _admission(route_key, target, snapshot, **overrides):
    credential_revision = _provider_credential_revision_from_snapshot(snapshot)
    assert credential_revision is not None
    revisions = market_v3_current_route_revisions(
        route_key,
        target,
        PROVIDER_MANIFESTS["hithink"],
        credential_revision=credential_revision,
    )
    assert revisions is not None
    return {
        "consumer": "thesis-ledger",
        "admissionState": "admitted",
        "scopeSymbols": [SYMBOL],
        "scopeDateFrom": START,
        "scopeDateTo": END,
        **revisions,
        **overrides,
    }


def _identity(route_key, target):
    return (
        canonical_route_key_json(route_key),
        target["providerId"],
        target["upstreamSource"],
    )


def _request(adjustment: str) -> ThesisLedgerDataRequest:
    return ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol=SYMBOL,
        timeframe="1d",
        start=START,
        end=END,
        instrument_type="STOCK",
        adjustment=adjustment,
        request_id=f"hithink-stock-{adjustment}-fixture",
    )


def _frame() -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {
                "date": day,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "amount": 10500.0,
            }
            for day in ("2026-05-18", "2026-05-19", "2026-05-20")
        ]
    )
    frame.attrs["upstream_source"] = HITHINK_STOCK_HISTORY_SOURCE
    frame.attrs["has_more_before"] = False
    return frame


class _Store:
    def __init__(
        self,
        route_key,
        target,
        snapshot,
        admissions=None,
        *,
        targets=None,
        second_snapshot=None,
        on_second_snapshot=None,
    ):
        self.route_key = route_key
        self.target = target
        self.snapshot = snapshot
        self.admissions = admissions or {}
        self.targets = targets or [
            {
                **target,
                "routeIndex": 0,
                "eligible": True,
                "reason": None,
            }
        ]
        self.second_snapshot = second_snapshot
        self.on_second_snapshot = on_second_snapshot
        self.snapshot_reads = 0
        self.admission_reads = 0
        self.health_updates = []

    def provider_credential_snapshot(self, provider_id):
        assert provider_id == "hithink"
        self.snapshot_reads += 1
        if self.snapshot_reads == 2:
            if self.on_second_snapshot:
                self.on_second_snapshot()
            if self.second_snapshot is not None:
                return self.second_snapshot
        return self.snapshot

    def get_route_admission_v3(self, *, key, target):
        self.admission_reads += 1
        value = self.admissions.get(_identity(key, target))
        return deepcopy(value) if value is not None else None

    def effective_policy_v3(self):
        return {
            "contractVersion": 3,
            "enabled": True,
            "revision": 3,
            "sourceDesiredRevision": 3,
            "routes": [{"key": self.route_key, "targets": deepcopy(self.targets)}],
        }

    def provider_registry(self):
        result = []
        for provider_id in ("akshare", "tencent", "hithink"):
            manifest = PROVIDER_MANIFESTS[provider_id]
            result.append(
                {
                    **manifest,
                    "configured": True,
                    "enabled": True,
                    "credentialConfigured": provider_id == "hithink",
                    "configVersion": 0,
                    "tombstone": None,
                }
            )
        return result

    def health(self, *_args, **_kwargs):
        return None

    def record_health(self, *args, **kwargs):
        self.health_updates.append((args, kwargs))


def _all_gated_stock_routes():
    return [
        (key, target)
        for key, target in iter_market_v3_gated_bar_adapters()
        if key["assetType"] == "STOCK" and target["providerId"] == "hithink"
    ]


def test_hithink_stock_routes_are_exact_gated_inventory_only():
    active = list(iter_market_v3_bar_adapters())
    gated = _all_gated_stock_routes()

    assert {key["adjustment"] for key, _target_value in gated} == {"none", "qfq", "hfq"}
    assert "STOCK" in PROVIDER_MANIFESTS["hithink"]["capabilities"]["DAILY_BAR"]
    stock_source = next(
        source
        for source in PROVIDER_MANIFESTS["hithink"]["upstreamSources"]
        if source["sourceId"] == HITHINK_STOCK_HISTORY_SOURCE
    )
    assert stock_source["capabilities"]["DAILY_BAR"] == ["STOCK"]
    assert all((key, target) not in active for key, target in gated)
    for key, target in gated:
        assert target == _target()
        assert market_v3_bar_adapter_reason(key, target) is None

    assert market_v3_bar_adapter_reason(
        _route_key("hfq"), _target("fund-market-historical")
    ) == "not_adapted"


@pytest.mark.parametrize("adjustment", ["none", "qfq", "hfq"])
def test_hithink_stock_catalog_and_pinned_data_use_one_exact_adjustment(
    monkeypatch, adjustment
):
    _set_synthetic_master(monkeypatch)
    route_key = _route_key(adjustment)
    target = _target()
    snapshot = _snapshot()
    admission = _admission(route_key, target, snapshot)
    store = _Store(route_key, target, snapshot, {_identity(route_key, target): admission})

    from src.services import thesis_ledger_hithink_stock as hithink_stock

    calls = []

    class _FakeStockAdapter:
        def __init__(self, *, api_key, timeout_seconds):
            calls.append({"kind": "init", "apiKey": api_key, "timeout": timeout_seconds})

        def fetch_daily_bars(
            self,
            symbol,
            start,
            end,
            requested_adjustment,
            *,
            expected_sessions,
            calendar_revision,
        ):
            calls.append(
                {
                    "kind": "fetch",
                    "symbol": symbol,
                    "start": start,
                    "end": end,
                    "adjustment": requested_adjustment,
                    "expectedSessions": expected_sessions,
                    "calendarRevision": calendar_revision,
                }
            )
            return _frame()

    monkeypatch.setattr(hithink_stock, "HiThinkStockHistoricalAdapter", _FakeStockAdapter)
    runtime = ThesisLedgerProviderRuntime(store)

    catalog = runtime.market_route_catalog_v3()
    catalog_route = next(
        row
        for row in catalog["entries"]
        if row["key"] == route_key and row["target"] == target
    )
    assert catalog["integrity"] == "complete"
    assert catalog_route["state"] == "ready"

    execution = runtime.execute_market_bars_v3(
        _request(adjustment),
        route_key,
        route_target={**target, "routeIndex": 0},
    )

    assert execution.provider == "hithink"
    assert execution.upstream_source == HITHINK_STOCK_HISTORY_SOURCE
    assert execution.route_index == 0
    assert len([call for call in calls if call["kind"] == "init"]) == 1
    init = next(call for call in calls if call["kind"] == "init")
    assert init["apiKey"] == TEST_API_KEY
    fetch = next(call for call in calls if call["kind"] == "fetch")
    assert fetch["symbol"] == SYMBOL
    assert fetch["adjustment"] == adjustment
    calendar = market_calendar_evidence_v3("CN", START, END)
    assert fetch["expectedSessions"] == tuple(calendar["expectedSessionDates"])
    assert fetch["calendarRevision"] == calendar["revision"]
    assert execution.value.attrs["thesis_ledger_v3_pagination"] == {
        "status": "complete",
        "pagesFetched": 1,
        "continuationPending": False,
        "requestedStart": START,
        "requestedEnd": END,
        "protocol": HITHINK_STOCK_SINGLE_RESPONSE_PROTOCOL_V1,
        "maximumRows": None,
    }


def test_hithink_stock_catalog_stays_not_admitted_without_current_g0(monkeypatch):
    _set_synthetic_master(monkeypatch)
    for adjustment in ("none", "qfq", "hfq"):
        route_key = _route_key(adjustment)
        target = _target()
        runtime = ThesisLedgerProviderRuntime(_Store(route_key, target, _snapshot()))
        catalog = runtime.market_route_catalog_v3()
        entry = next(
            row
            for row in catalog["entries"]
            if row["key"] == route_key and row["target"] == target
        )
        assert entry["state"] == "not_admitted"


@pytest.mark.parametrize(
    "admission_changes, catalog_state, error_code",
    [
        ({"credentialRevision": "stale-credential-v1"}, "not_admitted", "NO_ELIGIBLE_PROVIDER"),
        ({"sourceRevision": "stale-request-contract-v1"}, "not_admitted", "NO_ELIGIBLE_PROVIDER"),
        ({"adapterRevision": "stale-adapter-v1"}, "not_admitted", "NO_ELIGIBLE_PROVIDER"),
        ({"admissionState": "revoked"}, "not_admitted", "NO_ELIGIBLE_PROVIDER"),
        ({"scopeDateTo": "2026-05-19"}, "ready", "insufficient_coverage"),
    ],
)
def test_hithink_stock_catalog_and_data_reject_stale_or_narrow_admission(
    monkeypatch, admission_changes, catalog_state, error_code
):
    _set_synthetic_master(monkeypatch)
    route_key = _route_key("qfq")
    target = _target()
    snapshot = _snapshot()
    admission = _admission(route_key, target, snapshot, **admission_changes)
    store = _Store(route_key, target, snapshot, {_identity(route_key, target): admission})
    runtime = ThesisLedgerProviderRuntime(store)

    catalog = runtime.market_route_catalog_v3()
    catalog_route = next(
        row
        for row in catalog["entries"]
        if row["key"] == route_key and row["target"] == target
    )
    assert catalog_route["state"] == catalog_state

    calls = []
    from src.services import thesis_ledger_hithink_stock as hithink_stock

    class _FakeStockAdapter:
        def __init__(self, **_kwargs):
            pass

        def fetch_daily_bars(self, *_args, **_kwargs):
            calls.append(True)
            return _frame()

    monkeypatch.setattr(hithink_stock, "HiThinkStockHistoricalAdapter", _FakeStockAdapter)
    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(
            _request("qfq"),
            route_key,
            route_target={**target, "routeIndex": 0},
        )
    assert caught.value.code == error_code
    assert calls == []


@pytest.mark.parametrize("rotation", ["api_key", "master_key", "master_version"])
def test_hithink_stock_rechecks_current_credential_before_adapter_call(monkeypatch, rotation):
    _set_synthetic_master(monkeypatch)
    route_key = _route_key("qfq")
    target = _target()
    snapshot = _snapshot()
    admission = _admission(route_key, target, snapshot)
    second_snapshot = _snapshot("synthetic-hithink-key-after-rotation")

    def rotate():
        if rotation == "master_key":
            _set_synthetic_master(monkeypatch, "synthetic-master-after-rotation")
        elif rotation == "master_version":
            _set_synthetic_master(monkeypatch, version="test-v2")

    store = _Store(
        route_key,
        target,
        snapshot,
        {_identity(route_key, target): admission},
        second_snapshot=second_snapshot if rotation == "api_key" else None,
        on_second_snapshot=rotate,
    )
    runtime = ThesisLedgerProviderRuntime(store)
    calls = []
    from src.services import thesis_ledger_hithink_stock as hithink_stock

    class _FakeStockAdapter:
        def __init__(self, **_kwargs):
            calls.append("initialized")

        def fetch_daily_bars(self, *_args, **_kwargs):
            calls.append("fetched")
            return _frame()

    monkeypatch.setattr(hithink_stock, "HiThinkStockHistoricalAdapter", _FakeStockAdapter)

    with pytest.raises(ProviderCallError):
        runtime.execute_market_bars_v3(
            _request("qfq"),
            route_key,
            route_target={**target, "routeIndex": 0},
        )

    assert store.snapshot_reads == 2
    assert calls == []


def test_hithink_stock_pinned_failure_does_not_fallback_to_secondary_target(monkeypatch):
    _set_synthetic_master(monkeypatch)
    route_key = _route_key("qfq")
    target = _target()
    snapshot = _snapshot()
    admission = _admission(route_key, target, snapshot)
    backup = {"providerId": "tencent", "upstreamSource": "tencent"}
    store = _Store(
        route_key,
        target,
        snapshot,
        {_identity(route_key, target): admission},
        targets=[
            {**target, "routeIndex": 0, "eligible": True, "reason": None},
            {**backup, "routeIndex": 1, "eligible": True, "reason": None},
        ],
    )
    runtime = ThesisLedgerProviderRuntime(store)
    from src.services import thesis_ledger_hithink_stock as hithink_stock

    class _FailingStockAdapter:
        def __init__(self, **_kwargs):
            pass

        def fetch_daily_bars(self, *_args, **_kwargs):
            raise hithink_stock.HiThinkStockError("upstream_failure", "synthetic failure")

    class _TencentAdapter:
        calls = []

        def get_daily_data_for_source(self, *_args, **_kwargs):
            self.calls.append(True)
            return _frame()

    tencent_adapter = _TencentAdapter()
    runtime.adapters["tencent"] = tencent_adapter
    monkeypatch.setattr(hithink_stock, "HiThinkStockHistoricalAdapter", _FailingStockAdapter)

    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(
            _request("qfq"),
            route_key,
            route_target={**target, "routeIndex": 0},
        )

    assert caught.value.code == "upstream_failure"
    assert tencent_adapter.calls == []


def test_hithink_stock_source_revision_is_local_request_contract(monkeypatch):
    _set_synthetic_master(monkeypatch)
    snapshot = _snapshot()
    credential_revision = _provider_credential_revision_from_snapshot(snapshot)
    revisions_by_adjustment = {
        market_v3_current_route_revisions(
            _route_key(adjustment),
            _target(),
            PROVIDER_MANIFESTS["hithink"],
            credential_revision=credential_revision,
        )["adapterRevision"]
        for adjustment in ("none", "qfq", "hfq")
    }

    assert len(revisions_by_adjustment) == 3
    for adjustment in ("none", "qfq", "hfq"):
        revisions = market_v3_current_route_revisions(
            _route_key(adjustment),
            _target(),
            PROVIDER_MANIFESTS["hithink"],
            credential_revision=credential_revision,
        )
        assert revisions["sourceRevision"] == HITHINK_STOCK_SOURCE_CONTRACT_REVISION
        assert "upstream" not in revisions["sourceRevision"]
