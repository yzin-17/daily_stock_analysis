"""ThesisLedger Control Contract V2 的 source-pinned 路由回归。"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataRequest,
    ThesisLedgerProviderRuntime,
)


class _Frame:
    empty = False
    columns = ("date", "open", "high", "low", "close", "volume", "amount")

    def __init__(self, rows):
        self._rows = rows
        self.attrs = {}

    def iterrows(self):
        return iter(enumerate(self._rows))


def _frame():
    return _Frame(
        [
            {
                "date": "2025-01-02",
                "open": 10,
                "high": 11,
                "low": 9,
                "close": 10.5,
                "volume": 100,
                "amount": 1000,
            }
        ]
    )


def test_every_manifest_capability_has_an_executable_unique_source():
    for provider_id, manifest in PROVIDER_MANIFESTS.items():
        source_ids = {item["sourceId"] for item in manifest["upstreamSources"]}
        assert source_ids, provider_id
        assert len(source_ids) == len(manifest["upstreamSources"]), provider_id
        for capability, instrument_types in manifest["capabilities"].items():
            assert capability in {"REALTIME_QUOTE", "DAILY_BAR", "FUND_NAV", "FUND_NAV_HISTORY", "FUND_HOLDINGS", "CHIP_SUMMARY"}
            assert instrument_types, f"{provider_id}/{capability} has no executable instrument type"
            assert any(
                set(instrument_types) <= set(source["capabilities"].get(capability, []))
                for source in manifest["upstreamSources"]
            ), f"{provider_id}/{capability} has no executable source"
        for source in manifest["upstreamSources"]:
            source_id = source["sourceId"]
            source_capabilities = source["capabilities"]
            assert source_capabilities, f"{provider_id}/{source_id} has no source capability map"
            if source_id == provider_id:
                assert set(source_capabilities) == set(manifest["capabilities"])
            else:
                assert set(source_capabilities) <= {"DAILY_BAR"}
            for capability, instrument_types in source_capabilities.items():
                assert set(instrument_types) <= set(manifest["capabilities"].get(capability, []))
                assert instrument_types, f"{provider_id}/{source_id}/{capability} has no dispatchable type"


def _store(tmp_path, routes):
    stock_store = ThesisLedgerControlStore(str(tmp_path / "stock.db"))
    stock_store.apply_policy_v2(
        {
            "contractVersion": 2,
            "consumer": "thesis-ledger",
            "requestId": "route-v2-test",
            "revision": 1,
            "enabled": True,
            "routes": routes,
        }
    )
    return stock_store


def test_v2_policy_rejects_unknown_source_and_more_than_two_targets(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "invalid.db"))
    base = {
        "contractVersion": 2,
        "consumer": "thesis-ledger",
        "requestId": "invalid-route",
        "revision": 1,
        "enabled": True,
    }
    with pytest.raises(Exception, match="未声明"):
        store.apply_policy_v2(
            {
                **base,
                "routes": {
                    "DAILY_BAR": {
                        "ETF": [{"providerId": "akshare", "upstreamSource": "unknown"}]
                    }
                },
            }
        )
    with pytest.raises(Exception, match="不支持 DAILY_BAR/ETF"):
        store.apply_policy_v2(
            {
                **base,
                "routes": {
                    "DAILY_BAR": {
                        "ETF": [{"providerId": "akshare", "upstreamSource": "sina"}]
                    }
                },
            }
        )
    stock_store = ThesisLedgerControlStore(str(tmp_path / "stock.db"))
    stock_store.apply_policy_v2(
        {
            **base,
            "routes": {
                "DAILY_BAR": {
                    "STOCK": [{"providerId": "akshare", "upstreamSource": "sina"}]
                }
            },
        }
    )
    with pytest.raises(Exception, match="最多两个"):
        store.apply_policy_v2(
            {
                **base,
                "routes": {
                    "DAILY_BAR": {
                        "ETF": [
                            {"providerId": "tencent", "upstreamSource": "tencent"},
                            {"providerId": "akshare", "upstreamSource": "eastmoney"},
                            {"providerId": "akshare", "upstreamSource": "sina"},
                        ]
                    }
                },
            }
        )


def test_v2_effective_policy_preserves_control_envelope_request_id(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "effective-envelope.db"))
    result = store.apply_policy_v2(
        {
            "contractVersion": 2,
            "consumer": "thesis-ledger",
            "requestId": "effective-envelope-test",
            "revision": 1,
            "enabled": True,
            "routes": {
                "DAILY_BAR": {
                    "ETF": [
                        {"providerId": "tencent", "upstreamSource": "tencent"}
                    ]
                }
            },
        }
    )

    assert result["effective"]["requestId"] == "effective-envelope-test"


def test_v2_runtime_pins_source_and_does_not_use_adapter_fallback(tmp_path):
    calls = []

    @dataclass
    class Adapter:
        def get_daily_data_for_source(self, symbol, source, **options):
            calls.append((symbol, source))
            if source != "eastmoney":
                raise AssertionError("source must be pinned")
            return _frame()

    store = _store(
        tmp_path,
        {"DAILY_BAR": {"ETF": [{"providerId": "akshare", "upstreamSource": "eastmoney"}]}},
    )
    result = ThesisLedgerProviderRuntime(store, adapters={"akshare": Adapter()}).execute_request(
        ThesisLedgerDataRequest("DAILY_BAR", "510300.SH", instrument_type="ETF", limit=1)
    )
    assert calls == [("510300", "eastmoney")]
    assert result.provider == "akshare"
    assert result.upstream_source == "eastmoney"
    assert result.route_index == 0


def test_v2_runtime_fallback_is_ordered_and_target_level(tmp_path):
    calls = []

    @dataclass
    class Primary:
        calls: int = 0

        def get_daily_data_for_source(self, symbol, source, **options):
            self.calls += 1
            calls.append(("primary", source))
            raise RuntimeError("primary unavailable")

    @dataclass
    class Fallback:
        def get_daily_data_for_source(self, symbol, source, **options):
            calls.append(("fallback", source))
            return _frame()

    store = _store(
        tmp_path,
        {
            "DAILY_BAR": {
                "ETF": [
                    {"providerId": "tencent", "upstreamSource": "tencent"},
                    {"providerId": "akshare", "upstreamSource": "eastmoney"},
                ]
            }
        },
    )
    primary = Primary()
    result = ThesisLedgerProviderRuntime(
        store,
        adapters={"tencent": primary, "akshare": Fallback()},
    ).execute_request(
        ThesisLedgerDataRequest("DAILY_BAR", "510300.SH", instrument_type="ETF", limit=1)
    )
    assert calls == [("primary", "tencent"), ("fallback", "eastmoney")]
    assert primary.calls == 1
    assert result.provider == "akshare"
    assert result.upstream_source == "eastmoney"
    assert result.route_index == 1
    assert result.fallback_used is True
    assert result.provenance["routeIndex"] == 1


def test_v2_runtime_forwards_adjustment_and_fails_closed_per_target(tmp_path):
    calls = []

    @dataclass
    class Akshare:
        def get_daily_data_for_source(self, symbol, source, **options):
            calls.append(("akshare", source, options["adjustment"]))
            return _frame()

    @dataclass
    class Tencent:
        def get_daily_data_for_source(self, symbol, source, **options):
            calls.append(("tencent", source, options.get("adjustment")))
            raise AssertionError("unsupported Tencent adjustment must not invoke adapter")

    store = _store(
        tmp_path,
        {
            "DAILY_BAR": {
                "ETF": [
                    {"providerId": "tencent", "upstreamSource": "tencent"},
                    {"providerId": "akshare", "upstreamSource": "eastmoney"},
                ]
            }
        },
    )
    result = ThesisLedgerProviderRuntime(
        store,
        adapters={"tencent": Tencent(), "akshare": Akshare()},
    ).execute_request(
        ThesisLedgerDataRequest(
            "DAILY_BAR", "510300.SH", instrument_type="ETF", limit=1, adjustment="hfq"
        )
    )
    assert calls == [("akshare", "eastmoney", "hfq")]
    assert result.provider == "akshare"
    assert result.upstream_source == "eastmoney"


def test_v2_runtime_tencent_qfq_is_primary_and_receives_target_deadline(tmp_path):
    calls = []

    @dataclass
    class Tencent:
        def get_daily_data_for_source(self, symbol, source, **options):
            calls.append((symbol, source, options))
            return _frame()

    store = _store(
        tmp_path,
        {"DAILY_BAR": {"ETF": [{"providerId": "tencent", "upstreamSource": "tencent"}]}},
    )
    result = ThesisLedgerProviderRuntime(store, adapters={"tencent": Tencent()}).execute_request(
        ThesisLedgerDataRequest(
            "DAILY_BAR", "510300.SH", instrument_type="ETF", limit=1, adjustment="qfq"
        )
    )

    assert calls == [
        (
            "510300",
            "tencent",
            {"days": 1, "adjustment": "qfq", "timeout_seconds": 4.5},
        )
    ]
    assert result.provider == "tencent"
    assert result.route_index == 0


def test_v2_runtime_tencent_none_is_primary_for_raw_etf_bars(tmp_path):
    calls = []

    @dataclass
    class Tencent:
        def get_daily_data_for_source(self, symbol, source, **options):
            calls.append((symbol, source, options))
            return _frame()

    store = _store(
        tmp_path,
        {"DAILY_BAR": {"ETF": [{"providerId": "tencent", "upstreamSource": "tencent"}]}},
    )
    result = ThesisLedgerProviderRuntime(store, adapters={"tencent": Tencent()}).execute_request(
        ThesisLedgerDataRequest(
            "DAILY_BAR", "159516.SZ", instrument_type="ETF", limit=1, adjustment="none"
        )
    )

    assert calls == [
        (
            "159516",
            "tencent",
            {"days": 1, "adjustment": "none", "timeout_seconds": 4.5},
        )
    ]
    assert result.provider == "tencent"
    assert result.route_index == 0


def test_v2_runtime_tencent_none_requires_source_pinned_adapter(tmp_path):
    @dataclass
    class Tencent:
        def get_daily_data(self, symbol, **options):
            raise AssertionError("unproven Tencent raw fallback must not be called")

    store = _store(
        tmp_path,
        {"DAILY_BAR": {"ETF": [{"providerId": "tencent", "upstreamSource": "tencent"}]}},
    )
    with pytest.raises(Exception, match="未证明支持 adjustment=none"):
        ThesisLedgerProviderRuntime(store, adapters={"tencent": Tencent()}).execute_request(
            ThesisLedgerDataRequest(
                "DAILY_BAR", "159516.SZ", instrument_type="ETF", limit=1, adjustment="none"
            )
        )


def test_v2_runtime_none_adjustment_is_explicit_and_not_raw_mode_alias(tmp_path):
    calls = []

    @dataclass
    class Akshare:
        def get_daily_data_for_source(self, symbol, source, **options):
            calls.append(options)
            return _frame()

    store = _store(
        tmp_path,
        {"DAILY_BAR": {"ETF": [{"providerId": "akshare", "upstreamSource": "eastmoney"}]}},
    )
    result = ThesisLedgerProviderRuntime(store, adapters={"akshare": Akshare()}).execute_request(
        ThesisLedgerDataRequest(
            "DAILY_BAR", "510300.SH", instrument_type="ETF", limit=1, adjustment="none"
        )
    )
    assert calls == [
        {
            "days": 1,
            "adjustment": "none",
            "timeout_seconds": 4.5,
        }
    ]
    assert result.upstream_source == "eastmoney"


def test_v2_health_is_scoped_by_source(tmp_path):
    store = _store(
        tmp_path,
        {
            "DAILY_BAR": {
                "ETF": [
                    {"providerId": "akshare", "upstreamSource": "eastmoney"},
                    {"providerId": "akshare", "upstreamSource": "tencent"},
                ]
            }
        },
    )
    store.record_health("akshare", "DAILY_BAR", "ETF", state="degraded", upstream_source="eastmoney")
    store.record_health("akshare", "DAILY_BAR", "ETF", state="healthy", upstream_source="tencent")
    assert store.health("akshare", "DAILY_BAR", "ETF", upstream_source="eastmoney")["state"] == "degraded"
    assert store.health("akshare", "DAILY_BAR", "ETF", upstream_source="tencent")["state"] == "healthy"
