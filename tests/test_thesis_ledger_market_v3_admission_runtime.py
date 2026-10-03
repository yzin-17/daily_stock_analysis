from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from src.services.thesis_ledger_control import PROVIDER_MANIFESTS
from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError,
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
    market_v3_current_route_revisions,
)
from src.services.thesis_ledger_route_admission_v3 import canonical_route_key_json


ROUTE_KEY = {
    "kind": "bar",
    "market": "CN",
    "assetType": "ETF",
    "capability": "DAILY_BAR",
    "timeframe": "1d",
    "adjustment": "qfq",
}
TARGET = {"providerId": "akshare", "upstreamSource": "eastmoney"}
REQUEST = ThesisLedgerDataRequest(
    capability="DAILY_BAR",
    symbol="159516.SZ",
    timeframe="1d",
    start="2026-05-16",
    end="2026-08-09",
    instrument_type="ETF",
    adjustment="qfq",
    request_id="market-v3-admission-runtime-test",
)


def _admission(key=ROUTE_KEY, target=TARGET, **overrides):
    revisions = market_v3_current_route_revisions(
        key,
        target,
        PROVIDER_MANIFESTS[target["providerId"]],
    )
    assert revisions is not None
    return {
        "consumer": "thesis-ledger",
        "admissionState": "admitted",
        "scopeSymbols": [REQUEST.symbol],
        "scopeDateFrom": REQUEST.start,
        "scopeDateTo": REQUEST.end,
        **revisions,
        **overrides,
    }


def _identity(key, target):
    return (
        canonical_route_key_json(key),
        target["providerId"],
        target["upstreamSource"],
    )


class _CatalogStore:
    def __init__(self, admissions=None):
        self.admissions = admissions or {}
        self.providers = [
            {
                **PROVIDER_MANIFESTS[provider_id],
                "configured": True,
                "enabled": True,
                "credentialConfigured": False,
                "tombstone": None,
            }
            for provider_id in ("akshare", "tencent")
        ]

    def provider_registry(self):
        return self.providers

    def get_route_admission_v3(self, *, key, target):
        value = self.admissions.get(_identity(key, target))
        return deepcopy(value) if value is not None else None


class _DataStore:
    def __init__(self, admissions):
        self.admissions = list(admissions)
        self.read_count = 0

    def effective_policy_v3(self):
        return {
            "contractVersion": 3,
            "enabled": True,
            "revision": 27,
            "routes": [
                {
                    "key": ROUTE_KEY,
                    "targets": [
                        {
                            **TARGET,
                            "routeIndex": 0,
                            "eligible": True,
                            "reason": None,
                        }
                    ],
                }
            ],
        }

    def get_route_admission_v3(self, *, key, target):
        assert key == ROUTE_KEY
        assert target == TARGET
        index = min(self.read_count, len(self.admissions) - 1)
        self.read_count += 1
        return deepcopy(self.admissions[index])


class _Adapter:
    def __init__(self):
        self.calls = []

    def get_daily_data_for_source(self, symbol, source, **options):
        self.calls.append((symbol, source, options))
        frame = pd.DataFrame([{"date": "2026-05-18"}])
        frame.attrs["upstream_source"] = source
        return frame


def test_catalog_adds_basic_tencent_prices_to_exact_reviewed_admissions():
    store = _CatalogStore({_identity(ROUTE_KEY, TARGET): _admission()})
    catalog = ThesisLedgerProviderRuntime(store).market_route_catalog_v3()

    ready = [entry for entry in catalog["entries"] if entry["state"] == "ready"]
    assert catalog["integrity"] == "complete"
    reviewed = [entry for entry in ready if entry["target"] == TARGET]
    assert len(reviewed) == 1
    assert reviewed[0]["key"] == ROUTE_KEY
    basic = [entry for entry in ready if entry["target"]["providerId"] == "tencent"]
    assert len(ready) == 4
    assert {entry["key"]["adjustment"] for entry in basic} == {"none", "qfq", "hfq"}
    assert all(entry["key"]["assetType"] == "ETF" for entry in basic)


@pytest.mark.parametrize(
    "field, stale_value",
    [
        ("adapterRevision", "old-adapter-v1"),
        ("sourceRevision", "old-endpoint-contract-v1"),
        ("credentialRevision", "not-required-fixture-v1"),
    ],
)
def test_catalog_keeps_stale_revision_not_admitted(field, stale_value):
    stale = _admission(**{field: stale_value})
    store = _CatalogStore({_identity(ROUTE_KEY, TARGET): stale})

    catalog = ThesisLedgerProviderRuntime(store).market_route_catalog_v3()

    exact_entry = next(
        entry
        for entry in catalog["entries"]
        if entry["key"] == ROUTE_KEY and entry["target"] == TARGET
    )
    assert exact_entry["state"] == "not_admitted"


@pytest.mark.parametrize(
    "admission_state",
    ["pending", "invalid", "revoked", "expired", "not_yet_valid"],
)
def test_catalog_requires_current_admitted_state(admission_state):
    admission = _admission(admissionState=admission_state)
    store = _CatalogStore({_identity(ROUTE_KEY, TARGET): admission})

    catalog = ThesisLedgerProviderRuntime(store).market_route_catalog_v3()

    exact_entry = next(
        entry
        for entry in catalog["entries"]
        if entry["key"] == ROUTE_KEY and entry["target"] == TARGET
    )
    assert exact_entry["state"] == "not_admitted"


def test_revision_resolver_fails_closed_for_unknown_or_credentialed_adapter():
    assert market_v3_current_route_revisions(
        ROUTE_KEY,
        {"providerId": "hithink", "upstreamSource": "hithink"},
        {"providerId": "hithink", "requiresCredential": True},
        credential_version=5,
    ) is None
    assert market_v3_current_route_revisions(
        ROUTE_KEY,
        TARGET,
        {**PROVIDER_MANIFESTS["akshare"], "requiresCredential": True},
    ) is None
    assert market_v3_current_route_revisions(
        ROUTE_KEY,
        TARGET,
        {**PROVIDER_MANIFESTS["akshare"], "requiresCredential": True},
        credential_version=7,
    )["credentialRevision"] == "7"


def test_tencent_etf_hfq_has_a_separate_revision_and_stock_hfq_stays_unadapted():
    target = {"providerId": "tencent", "upstreamSource": "tencent"}
    hfq_key = {**ROUTE_KEY, "adjustment": "hfq"}
    hfq = market_v3_current_route_revisions(
        hfq_key, target, PROVIDER_MANIFESTS["tencent"],
    )
    qfq = market_v3_current_route_revisions(
        ROUTE_KEY, target, PROVIDER_MANIFESTS["tencent"],
    )
    none = market_v3_current_route_revisions(
        {**ROUTE_KEY, "adjustment": "none"}, target, PROVIDER_MANIFESTS["tencent"],
    )
    stock_hfq = market_v3_current_route_revisions(
        {**hfq_key, "assetType": "STOCK"}, target, PROVIDER_MANIFESTS["tencent"],
    )

    assert hfq is not None and qfq is not None and none is not None
    assert hfq["adapterRevision"] == "dsa-v3-etf-tencent-hfq-newfqkline-adapter-v1"
    assert len({hfq["adapterRevision"], qfq["adapterRevision"], none["adapterRevision"]}) == 3
    assert stock_hfq is None


@pytest.mark.parametrize(
    "stale_admission",
    [
        _admission(sourceRevision="fixture-source-v1"),
        _admission(credentialRevision="not-required-fixture-v1"),
        _admission(admissionState="revoked"),
    ],
)
def test_data_request_rejects_stale_effective_before_adapter_call(stale_admission):
    adapter = _Adapter()
    runtime = ThesisLedgerProviderRuntime(_DataStore([stale_admission]), adapters={"akshare": adapter})

    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(REQUEST, ROUTE_KEY)

    assert caught.value.code == "NO_ELIGIBLE_PROVIDER"
    assert adapter.calls == []


def test_data_request_rechecks_admission_immediately_before_provider_call(monkeypatch):
    current = _admission()
    stale = _admission(adapterRevision="old-adapter-v1")
    store = _DataStore([current, stale])
    adapter = _Adapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": adapter})
    monkeypatch.setattr(runtime, "_validate_bars", lambda frame: frame)

    def execute_one_target(_capability, _instrument_type, operation, **kwargs):
        target = kwargs["route_targets_override"][0]
        return operation(target.provider_id, adapter, target.upstream_source)

    monkeypatch.setattr(runtime, "_execute_with_metadata", execute_one_target)

    with pytest.raises(ProviderCallError) as caught:
        runtime.execute_market_bars_v3(REQUEST, ROUTE_KEY)

    assert caught.value.code == "NO_ELIGIBLE_PROVIDER"
    assert store.read_count == 2
    assert adapter.calls == []
