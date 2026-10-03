from __future__ import annotations

from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, ThesisLedgerControlStore
from src.services.thesis_ledger_market_v3_adapters import (
    HITHINK_STOCK_HISTORY_SOURCE,
    iter_market_v3_bar_adapters,
    iter_market_v3_gated_bar_adapters,
    market_v3_bar_adapter_reason,
)
from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
    HITHINK_ETF_LOCAL_COVERAGE_PROTOCOL_V1,
    HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
    HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    market_pagination_contract_v3,
    market_source_contract_v3,
)
from src.services.thesis_ledger_hithink_etf import HITHINK_ETF_HISTORICAL_URL
from src.services.thesis_ledger_hithink_dividend_contract_v3 import HITHINK_DIVIDEND_SOURCE
from src.services.thesis_ledger_hithink_quote import (
    HITHINK_ETF_SNAPSHOT_SOURCE, HITHINK_STOCK_SNAPSHOT_SOURCE,
)


_HITHINK_KEY = "synthetic-fixture-key-never-use-for-provider"
_ROUTE_KEY = {
    "kind": "bar",
    "market": "CN",
    "assetType": "ETF",
    "capability": "DAILY_BAR",
    "timeframe": "1d",
    "adjustment": "qfq",
}
_ROUTE_TARGET = {
    "providerId": "hithink",
    "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
}


def test_hithink_etf_qfq_is_an_exact_gated_route_only():
    gated_routes = list(iter_market_v3_gated_bar_adapters())

    stock_gated_routes = [
        (key, target)
        for key, target in gated_routes
        if key["assetType"] == "STOCK" and target["providerId"] == "hithink"
    ]
    assert gated_routes[0] == (_ROUTE_KEY, _ROUTE_TARGET)
    assert [key["adjustment"] for key, _target in stock_gated_routes] == [
        "none",
        "qfq",
        "hfq",
    ]
    assert all(
        target["upstreamSource"] == HITHINK_STOCK_HISTORY_SOURCE
        for _key, target in stock_gated_routes
    )
    assert not any(
        target["providerId"] == "hithink"
        for _key, target in iter_market_v3_bar_adapters()
    )
    assert market_v3_bar_adapter_reason(_ROUTE_KEY, _ROUTE_TARGET) is None

    for adjustment in ("none", "hfq"):
        assert market_v3_bar_adapter_reason(
            {**_ROUTE_KEY, "adjustment": adjustment}, _ROUTE_TARGET
        ) == "unsupported_adjustment"
    assert market_v3_bar_adapter_reason(
        {**_ROUTE_KEY, "assetType": "STOCK"}, _ROUTE_TARGET
    ) == "not_adapted"
    assert market_v3_bar_adapter_reason(
        _ROUTE_KEY, {**_ROUTE_TARGET, "upstreamSource": "eastmoney"}
    ) == "not_adapted"


def test_hithink_history_contract_separates_local_protocol_from_unknown_upstream_revisions():
    contract = market_source_contract_v3(
        "ETF", "hithink", HITHINK_ETF_HISTORY_SOURCE
    )

    assert contract == {
        "providerId": "hithink",
        "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
        "endpoint": HITHINK_ETF_HISTORICAL_URL,
        "sourceContractRevision": HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
        "paginationProtocol": HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
        "continuationCursorSupported": False,
        "maximumRows": None,
        "maximumRowsKnown": False,
        "upstreamPaginationVerified": False,
        "requestWindowMaximumYears": 5,
        "coverageProtocol": HITHINK_ETF_LOCAL_COVERAGE_PROTOCOL_V1,
        "upstreamCoverageVerified": False,
        "upstreamDataRevision": None,
        "adjustmentAlgorithmRevision": None,
    }
    assert market_pagination_contract_v3(
        "ETF", "hithink", HITHINK_ETF_HISTORY_SOURCE
    ) == {
        "protocol": HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
        "maximumRows": None,
    }
    assert market_source_contract_v3("ETF", "hithink", "eastmoney") is None
    assert market_source_contract_v3("STOCK", "hithink", HITHINK_ETF_HISTORY_SOURCE) is None


def test_hithink_basic_price_effective_is_ready_with_environment_key(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HITHINK_API_KEY", _HITHINK_KEY)
    manifest = PROVIDER_MANIFESTS["hithink"]
    assert manifest["providerId"] == "hithink"
    assert manifest["requiresCredential"] is True
    assert manifest["credentialEnvironmentKey"] == "HITHINK_API_KEY"
    assert manifest["configurationMode"] == "dsa_environment"
    assert manifest["markets"] == ["CN"]
    assert manifest["capabilities"] == {
        "DAILY_BAR": ["ETF", "STOCK"],
        "REALTIME_QUOTE": ["ETF", "STOCK"],
        "CASH_DISTRIBUTION": ["ETF"],
    }
    assert manifest["upstreamSources"] == [
        {
            "sourceId": HITHINK_ETF_HISTORY_SOURCE,
            "displayName": "HiThink ETF 历史接口",
            "capabilities": {"DAILY_BAR": ["ETF"]},
        },
        {
            "sourceId": HITHINK_STOCK_HISTORY_SOURCE,
            "displayName": "HiThink 股票历史接口",
            "capabilities": {"DAILY_BAR": ["STOCK"]},
        },
        {
            "sourceId": HITHINK_STOCK_SNAPSHOT_SOURCE,
            "displayName": "HiThink A股行情快照",
            "capabilities": {"REALTIME_QUOTE": ["STOCK"]},
        },
        {
            "sourceId": HITHINK_ETF_SNAPSHOT_SOURCE,
            "displayName": "HiThink ETF/LOF 场内快照",
            "capabilities": {"REALTIME_QUOTE": ["ETF"]},
        },
        {
            "sourceId": HITHINK_DIVIDEND_SOURCE,
            "displayName": "HiThink 场内基金分红",
            "capabilities": {"CASH_DISTRIBUTION": ["ETF"]},
        },
    ]

    store = ThesisLedgerControlStore(str(tmp_path / "hithink-market-v3.db"))
    registry_item = next(
        item for item in store.provider_registry() if item["providerId"] == "hithink"
    )
    assert registry_item["configured"] is True
    assert registry_item["credentialConfigured"] is True
    assert _HITHINK_KEY not in repr(registry_item)

    applied = store.apply_policy_v3(
        {
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "requestId": "hithink-fixture-policy",
            "revision": 1,
            "enabled": True,
            "routes": [{"key": _ROUTE_KEY, "targets": [_ROUTE_TARGET]}],
        }
    )
    target = applied["effective"]["routes"][0]["targets"][0]
    assert target["eligible"] is True
    assert target["reason"] is None
