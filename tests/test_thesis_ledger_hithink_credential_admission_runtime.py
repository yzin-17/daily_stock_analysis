from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS,
    _provider_credential_revision_from_snapshot,
)
from src.services.thesis_ledger_hithink_etf import HiThinkETFBar, HiThinkETFBarSeries
from src.services.thesis_ledger_hithink_etf_units import hithink_etf_field_contract
from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
    HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
)
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
    market_v3_current_route_revisions,
)


ROUTE_KEY = {
    "kind": "bar",
    "market": "CN",
    "assetType": "ETF",
    "capability": "DAILY_BAR",
    "timeframe": "1d",
    "adjustment": "qfq",
}
TARGET = {"providerId": "hithink", "upstreamSource": HITHINK_ETF_HISTORY_SOURCE}
SYMBOL = "159516.SZ"
START = "2026-05-18"
END = "2026-05-20"
TEST_API_KEY = "synthetic-current-hithink-key"


def _snapshot(api_key: str) -> ProviderCredentialSnapshot:
    return ProviderCredentialSnapshot.create(
        "hithink",
        "environment",
        "api_key",
        {"apiKey": api_key} if api_key else {},
        config_version=0,
        credential_version=0,
    )


def _set_synthetic_master(monkeypatch, key: str = "synthetic-dsa-master", version: str = "test-v1"):
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", key)
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY_VERSION", version)
    monkeypatch.delenv("DSA_SECRET_KEY", raising=False)


def _admission(snapshot: ProviderCredentialSnapshot) -> dict:
    credential_revision = _provider_credential_revision_from_snapshot(snapshot)
    assert credential_revision is not None
    revisions = market_v3_current_route_revisions(
        ROUTE_KEY,
        TARGET,
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
    }


def _frame() -> pd.DataFrame:
    result = pd.DataFrame([{"date": START}])
    result.attrs["upstream_source"] = HITHINK_ETF_HISTORY_SOURCE
    result.attrs["has_more_before"] = False
    return result


def _request() -> ThesisLedgerDataRequest:
    return ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol=SYMBOL,
        timeframe="1d",
        start=START,
        end=END,
        instrument_type="ETF",
        adjustment="qfq",
        request_id="hithink-credential-admission-test",
    )


class _AdmissionStore:
    def __init__(self, snapshot, admission, *, second_snapshot=None, on_second_snapshot=None):
        self.snapshot = snapshot
        self.admission = admission
        self.second_snapshot = second_snapshot
        self.on_second_snapshot = on_second_snapshot
        self.snapshot_reads = 0
        self.admission_reads = 0
        self.health_updates = []
        self.targets = [
            {
                **TARGET,
                "routeIndex": 0,
                "eligible": True,
                "reason": None,
            }
        ]

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
        if key != ROUTE_KEY or target != TARGET:
            return None
        self.admission_reads += 1
        return deepcopy(self.admission)

    def effective_policy_v3(self):
        return {
            "contractVersion": 3,
            "enabled": True,
            "revision": 11,
            "sourceDesiredRevision": 11,
            "routes": [{"key": ROUTE_KEY, "targets": deepcopy(self.targets)}],
        }

    def provider_registry(self):
        return [
            {
                **PROVIDER_MANIFESTS["hithink"],
                "configured": True,
                "enabled": True,
                "credentialConfigured": True,
                "configVersion": 0,
                "tombstone": None,
            }
        ]

    def health(self, *_args, **_kwargs):
        return None

    def record_health(self, *args, **kwargs):
        self.health_updates.append((args, kwargs))


def _runtime(monkeypatch, store):
    runtime = ThesisLedgerProviderRuntime(store)
    calls = []

    def fake_hithink_fetch(request, *, credential_snapshot=None):
        calls.append((request, credential_snapshot))
        return _frame()

    monkeypatch.setattr(runtime, "_fetch_hithink_etf_bars", fake_hithink_fetch)
    monkeypatch.setattr(runtime, "_validate_bars", lambda frame: frame)
    return runtime, calls


def _execute(runtime):
    return runtime.execute_market_bars_v3(
        _request(),
        ROUTE_KEY,
        route_target={**TARGET, "routeIndex": 0},
    )


def test_hithink_fetch_projects_only_reviewed_native_units_without_conversion(monkeypatch):
    import src.services.thesis_ledger_hithink_etf as hithink_etf

    series = HiThinkETFBarSeries(
        symbol=SYMBOL,
        requested_start=START,
        requested_end=END,
        bars=(HiThinkETFBar(
            date=START, open=1.0, high=1.1, low=0.9, close=1.05,
            volume=1000.0, amount=1050.0,
        ),),
        coverage={},
        response_fingerprint="a" * 64,
    )
    monkeypatch.setattr(hithink_etf, "fetch_hithink_etf_daily_bars", lambda **_kwargs: series)
    runtime = ThesisLedgerProviderRuntime(store=object())

    frame = runtime._fetch_hithink_etf_bars(
        _request(), credential_snapshot=_snapshot(TEST_API_KEY)
    )

    assert frame.attrs["hithink_etf_field_units"] == hithink_etf_field_contract(
        symbol=SYMBOL, start=START, end=END,
        source_revision=HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    )
    assert frame.attrs["hithink_etf_field_units"]["volumeUnit"] == "fund-unit"
    assert frame.attrs["hithink_etf_field_units"]["amountUnit"] == "CNY"
    assert frame.loc[0, "volume"] == 1000.0
    assert frame.loc[0, "amount"] == 1050.0


@pytest.mark.parametrize("empty", [False, True])
def test_admitted_tradability_path_preserves_sparse_source_evidence(monkeypatch, empty):
    from datetime import date, datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    import src.services.thesis_ledger_hithink_etf as adapter
    from src.services.thesis_ledger_dependency_facts import instrument_facts_response

    _set_synthetic_master(monkeypatch)
    snapshot = _snapshot(TEST_API_KEY)
    runtime = ThesisLedgerProviderRuntime(_AdmissionStore(snapshot, _admission(snapshot)))
    dates = [] if empty else [START, END]
    items = [{
        "date_ms": int(datetime.combine(date.fromisoformat(day), datetime.min.time(), tzinfo=ZoneInfo("Asia/Shanghai")).timestamp() * 1000),
        "open_price": 1, "high_price": 1.2, "low_price": 0.9, "close_price": 1.1,
        "volume": 100, "turnover": 110,
    } for day in dates]

    def source(**kwargs):
        assert kwargs["allow_missing_sessions"] is True
        return adapter.parse_hithink_etf_daily_response(
            {"code": 0, "data": {"thscode": SYMBOL, "interval": "1d", "adjust": None,
                                  "timestamp": items[-1]["date_ms"] if items else None, "item": items}},
            symbol=SYMBOL, start=START, end=END, calendar_evidence=kwargs["calendar_evidence"],
            response_fingerprint="a" * 64, allow_missing_sessions=True,
        )

    monkeypatch.setattr(adapter, "fetch_hithink_etf_daily_bars", source)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    response = instrument_facts_response(
        SYMBOL, datetime.now(timezone.utc) + timedelta(minutes=1), START, END, START, END, [],
        instrument_type="ETF", route_key=ROUTE_KEY, route_target={**TARGET, "routeIndex": 0},
    )
    assert response["status"] == "supported"
    assert response["coverage"]["complete"] is True
    states = [day["state"] for day in response["historicalTradability"]["days"]]
    assert states == (["assumed-untradable-no-bar"] * 3 if empty else [
        "observed-traded", "assumed-untradable-no-bar", "observed-traded",
    ])
    assert response["historicalTradability"]["barSource"]["responseSha256"] == "a" * 64
    assert TEST_API_KEY not in str(response)

    from api.thesis_ledger import _market_data_v3_response, _market_data_v3_coverage_context
    from api.thesis_ledger_multi_window_v3 import execute_market_window_v3
    request = {"contractVersion": 3, "requestId": "sparse-price", "symbol": SYMBOL,
               "routeKey": ROUTE_KEY, "routeTarget": {**TARGET, "routeIndex": 0},
               "start": START, "end": END, "tradabilityMode": "assume-untradable-no-bar"}
    execution = execute_market_window_v3(request, runtime)
    price = _market_data_v3_response(request, execution, _market_data_v3_coverage_context(request))
    assert len(price["bars"]) == (0 if empty else 2)
    assert [day["state"] for day in price["historicalTradabilityWindows"][0]["days"]] == states
    assert price["coverage"]["actualStart"] is None if empty else price["coverage"]["actualStart"] is not None
    assert TEST_API_KEY not in str(price)


def test_hithink_basic_catalog_and_data_use_current_credential_without_admission(monkeypatch):
    _set_synthetic_master(monkeypatch)
    snapshot = _snapshot(TEST_API_KEY)
    store = _AdmissionStore(snapshot, _admission(snapshot))
    runtime, calls = _runtime(monkeypatch, store)

    catalog = runtime.market_route_catalog_v3()
    entry = next(
        item
        for item in catalog["entries"]
        if item["key"] == ROUTE_KEY and item["target"] == TARGET
    )
    assert entry["state"] == "ready"
    assert _provider_credential_revision_from_snapshot(snapshot) not in str(catalog)
    assert TEST_API_KEY not in str(catalog)
    catalog_snapshot_reads = store.snapshot_reads
    catalog_admission_reads = store.admission_reads

    execution = _execute(runtime)

    assert execution.provider == "hithink"
    assert execution.route_index == 0
    assert len(calls) == 1
    assert calls[0][0] == _request()
    assert calls[0][1] is snapshot
    assert store.snapshot_reads == catalog_snapshot_reads + 1
    assert store.admission_reads == catalog_admission_reads


def test_hithink_basic_prices_ignore_stale_manual_admission(monkeypatch):
    _set_synthetic_master(monkeypatch)
    snapshot = _snapshot(TEST_API_KEY)
    stale_admission = {**_admission(snapshot), "credentialRevision": "5"}
    store = _AdmissionStore(snapshot, stale_admission)
    runtime, calls = _runtime(monkeypatch, store)

    catalog = runtime.market_route_catalog_v3()
    entry = next(
        item
        for item in catalog["entries"]
        if item["key"] == ROUTE_KEY and item["target"] == TARGET
    )
    assert entry["state"] == "ready"
    assert _execute(runtime).provider == "hithink"
    assert len(calls) == 1
    assert calls[0][1] is snapshot


@pytest.mark.parametrize("rotation", ["api_key", "master_key", "master_version"])
def test_hithink_basic_data_uses_rotated_key_without_manual_readmission(
    monkeypatch, rotation
):
    _set_synthetic_master(monkeypatch)
    initial_snapshot = _snapshot(TEST_API_KEY)
    admission = _admission(initial_snapshot)
    rotated_snapshot = _snapshot("synthetic-rotated-hithink-key")

    def rotate_master():
        if rotation == "master_key":
            _set_synthetic_master(monkeypatch, "synthetic-rotated-dsa-master")
        elif rotation == "master_version":
            _set_synthetic_master(monkeypatch, version="test-v2")

    store = _AdmissionStore(
        initial_snapshot,
        admission,
        second_snapshot=rotated_snapshot if rotation == "api_key" else initial_snapshot,
        on_second_snapshot=rotate_master,
    )
    runtime, calls = _runtime(monkeypatch, store)

    assert runtime._catalog_market_v3_admission_is_current(ROUTE_KEY, TARGET, PROVIDER_MANIFESTS["hithink"])
    execution = _execute(runtime)
    assert execution.provider == "hithink"
    assert store.snapshot_reads == 2
    assert store.admission_reads == 0
    assert len(calls) == 1
    expected_snapshot = rotated_snapshot if rotation == "api_key" else initial_snapshot
    assert calls[0][1] is expected_snapshot
    assert TEST_API_KEY not in str(execution)


@pytest.mark.parametrize("missing", ["api_key", "master_key"])
def test_hithink_basic_prices_require_api_key_but_not_audit_master_key(
    monkeypatch, missing
):
    _set_synthetic_master(monkeypatch)
    admitted_snapshot = _snapshot(TEST_API_KEY)
    admission = _admission(admitted_snapshot)
    current_snapshot = _snapshot("") if missing == "api_key" else admitted_snapshot
    if missing == "master_key":
        monkeypatch.delenv("THESIS_LEDGER_DSA_SECRET_KEY", raising=False)
        monkeypatch.delenv("DSA_SECRET_KEY", raising=False)
    store = _AdmissionStore(current_snapshot, admission)
    runtime, calls = _runtime(monkeypatch, store)

    catalog = runtime.market_route_catalog_v3()
    entry = next(
        item
        for item in catalog["entries"]
        if item["key"] == ROUTE_KEY and item["target"] == TARGET
    )
    if missing == "api_key":
        assert entry["state"] == "not_admitted"
        with pytest.raises(ProviderCallError) as caught:
            _execute(runtime)
        assert caught.value.code == "NO_ELIGIBLE_PROVIDER"
        assert calls == []
    else:
        assert entry["state"] == "ready"
        assert _execute(runtime).provider == "hithink"
        assert len(calls) == 1


def test_hithink_current_route_revisions_need_internal_hmac_revision():
    assert market_v3_current_route_revisions(
        ROUTE_KEY,
        TARGET,
        PROVIDER_MANIFESTS["hithink"],
    ) is None
    assert market_v3_current_route_revisions(
        ROUTE_KEY,
        TARGET,
        PROVIDER_MANIFESTS["hithink"],
        credential_version=17,
    ) is None
