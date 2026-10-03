from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.middlewares.error_handler import add_error_handlers
from api.thesis_ledger import (
    MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS,
    _market_data_v3_pagination_proof,
    router_v3,
)
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, ThesisLedgerControlStore
from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
    HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
    HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    market_pagination_contract_v3,
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
_TEST_ONLY_KEY = "hithink-test-credential-must-not-escape"


class _NeverCallAdapter:
    def __init__(self):
        self.calls = []

    def get_market_bars_v3(self, *_args, **_kwargs):
        self.calls.append((_args, _kwargs))
        raise AssertionError("dormant HiThink adapter must not be called")


def _policy():
    return {
        "contractVersion": 3,
        "consumer": "thesis-ledger",
        "requestId": "hithink-dormant-v3-policy",
        "revision": 1,
        "enabled": True,
        "routes": [{"key": ROUTE_KEY, "targets": [TARGET]}],
    }


def test_hithink_api_allowlist_and_local_pagination_contract_are_registered():
    contract = market_pagination_contract_v3(
        "ETF", "hithink", HITHINK_ETF_HISTORY_SOURCE
    )
    assert "hithink" in MARKET_DATA_V3_SUPPORTED_PROVIDER_IDS
    assert contract == {
        "protocol": HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
        "maximumRows": None,
    }

    frame = SimpleNamespace(
        attrs={
            "thesis_ledger_v3_pagination": {
                "status": "complete",
                "pagesFetched": 1,
                "continuationPending": False,
                **contract,
                "requestedStart": START,
                "requestedEnd": END,
            }
        }
    )
    proof = _market_data_v3_pagination_proof(
        frame,
        asset_type="ETF",
        provider="hithink",
        upstream_source=HITHINK_ETF_HISTORY_SOURCE,
        expected_session_count=2,
        requested_start=START,
        requested_end=END,
        request_id="hithink-pagination-fixture",
    )
    assert proof == {
        "status": "complete",
        "pagesFetched": 1,
        "continuationPending": False,
    }


def test_hithink_disabled_policy_prevents_call_despite_ready_basic_catalog(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("HITHINK_API_KEY", _TEST_ONLY_KEY)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "dsa-test-token")

    store = ThesisLedgerControlStore(str(tmp_path / "hithink-dormant-v3.sqlite"))
    applied = store.apply_policy_v3({**_policy(), "enabled": False})
    effective_target = applied["effective"]["routes"][0]["targets"][0]
    assert effective_target["eligible"] is False
    assert effective_target["reason"] == "disabled"

    registry_item = next(
        item for item in store.provider_registry() if item["providerId"] == "hithink"
    )
    assert registry_item["configured"] is True
    assert "credentialVersion" not in registry_item
    assert market_v3_current_route_revisions(
        ROUTE_KEY,
        TARGET,
        PROVIDER_MANIFESTS["hithink"],
    ) is None

    spy = _NeverCallAdapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"hithink": spy})
    catalog = runtime.market_route_catalog_v3()
    hithink_entries = [
        entry
        for entry in catalog["entries"]
        if entry["target"] == TARGET and entry["key"] == ROUTE_KEY
    ]
    assert catalog["integrity"] == "complete"
    assert hithink_entries == [{"key": ROUTE_KEY, "target": TARGET, "state": "ready"}]
    assert HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1 not in json.dumps(catalog)
    assert _TEST_ONLY_KEY not in json.dumps(catalog)

    request = ThesisLedgerDataRequest(
        capability="DAILY_BAR",
        symbol=SYMBOL,
        timeframe="1d",
        start=START,
        end=END,
        instrument_type="ETF",
        adjustment="qfq",
        request_id="hithink-dormant-v3-data",
    )
    with pytest.raises(ProviderCallError) as error:
        runtime.execute_market_bars_v3(
            request,
            ROUTE_KEY,
            route_target={**TARGET, "routeIndex": 0},
        )
    assert error.value.code == "NO_ELIGIBLE_PROVIDER"
    assert spy.calls == []
    assert _TEST_ONLY_KEY not in str(error.value)

    import src.services.thesis_ledger_provider_runtime as provider_runtime

    monkeypatch.setattr(provider_runtime, "get_thesis_ledger_runtime", lambda: runtime)
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    add_error_handlers(app)
    response = TestClient(app).post(
        "/api/v3/thesis-ledger/market/bars",
        headers={"Authorization": "Bearer dsa-test-token"},
        json={
            "contractVersion": 3,
            "requestId": "hithink-dormant-v3-api",
            "symbol": SYMBOL,
            "routeKey": ROUTE_KEY,
            "start": START,
            "end": END,
            "routeTarget": {**TARGET, "routeIndex": 0},
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "upstream_failure"
    assert _TEST_ONLY_KEY not in response.text
    assert spy.calls == []
