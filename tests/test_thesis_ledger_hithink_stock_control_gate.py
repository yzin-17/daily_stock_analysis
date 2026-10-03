"""HiThink stock Control routes remain explicitly unavailable until admitted."""

from __future__ import annotations

from src.services.thesis_ledger_control import (
    PROVIDER_MANIFESTS,
    ThesisLedgerControlStore,
)
from src.services.thesis_ledger_market_v3_adapters import HITHINK_STOCK_HISTORY_SOURCE
from src.services.thesis_ledger_market_v3_facts import HITHINK_ETF_HISTORY_SOURCE


def test_hithink_manifest_keeps_etf_and_stock_source_capabilities_separate():
    manifest = PROVIDER_MANIFESTS["hithink"]
    assert manifest["capabilities"]["DAILY_BAR"] == ["ETF", "STOCK"]
    sources = {source["sourceId"]: source for source in manifest["upstreamSources"]}
    assert sources[HITHINK_ETF_HISTORY_SOURCE]["capabilities"] == {
        "DAILY_BAR": ["ETF"]
    }
    assert sources[HITHINK_STOCK_HISTORY_SOURCE]["capabilities"] == {
        "DAILY_BAR": ["STOCK"]
    }


def test_hithink_v3_stock_none_qfq_hfq_desired_is_retained_but_not_admitted(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HITHINK_API_KEY", "synthetic-hithink-test-key")
    store = ThesisLedgerControlStore(str(tmp_path / "hithink-v3.db"))
    routes = [
        {
            "key": {
                "kind": "bar",
                "market": "CN",
                "assetType": "STOCK",
                "capability": "DAILY_BAR",
                "timeframe": "1d",
                "adjustment": adjustment,
            },
            "targets": [
                {
                    "providerId": "hithink",
                    "upstreamSource": HITHINK_STOCK_HISTORY_SOURCE,
                }
            ],
        }
        for adjustment in ("none", "qfq", "hfq")
    ]

    applied = store.apply_policy_v3(
        {
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "requestId": "hithink-v3-stock-gate",
            "revision": 1,
            "enabled": True,
            "routes": routes,
        }
    )

    assert applied["desired"]["routes"] == routes
    for route in applied["effective"]["routes"]:
        assert route["targets"][0]["eligible"] is False
        assert route["targets"][0]["reason"] == "not_admitted"
        assert route["reason"] == "not_admitted"
