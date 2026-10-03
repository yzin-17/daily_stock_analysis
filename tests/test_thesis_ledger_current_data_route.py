from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from tests.current_data_policy_fixture import apply_data_policy

from src.services.thesis_ledger_current_data_route import (
    CurrentDataRouteError, current_data_adapter_revisions,
    select_current_data_targets,
)
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import (
    ThesisLedgerDataRequest, ThesisLedgerGatewayError, ThesisLedgerProviderRuntime,
)


def _policy():
    return {
        "contractVersion": 3,
        "revision": 7,
        "sourceDesiredRevision": 7,
        "enabled": True,
        "routes": [{
            "key": {
                "kind": "data", "market": "CN", "assetType": "ETF",
                "capability": "REALTIME_QUOTE",
            },
            "targets": [
                {"providerId": "akshare", "upstreamSource": "eastmoney", "routeIndex": 0,
                 "eligible": False, "reason": "not_admitted"},
                {"providerId": "hithink", "upstreamSource": "fund-market-snapshot",
                 "routeIndex": 1, "eligible": True, "reason": None},
            ],
        }],
    }


def test_current_data_route_preserves_target_order_and_exact_source():
    assert select_current_data_targets(
        _policy(), capability="REALTIME_QUOTE", instrument_type="ETF",
        symbol="510300.SH",
    ) == [("hithink", "fund-market-snapshot", 1)]


@pytest.mark.parametrize("change,code", [
    (lambda policy: policy.update(contractVersion=2), "NO_ELIGIBLE_PROVIDER"),
    (lambda policy: policy.update(sourceDesiredRevision=6), "invalid_response"),
    (lambda policy: policy["routes"][0]["targets"][1].update(routeIndex=0), "invalid_response"),
    (lambda policy: policy["routes"][0]["targets"][1].update(routeIndex=True), "invalid_response"),
    (lambda policy: policy["routes"][0]["targets"][1].update(eligible=False), "invalid_response"),
    (lambda policy: policy["routes"].clear(), "NO_ELIGIBLE_PROVIDER"),
])
def test_current_data_route_rejects_old_or_inconsistent_state(change, code):
    policy = _policy()
    change(policy)
    with pytest.raises(CurrentDataRouteError) as raised:
        select_current_data_targets(
            policy, capability="REALTIME_QUOTE", instrument_type="ETF",
            symbol="510300.SH",
        )
    assert raised.value.code == code


def test_quote_catalog_uses_exact_stock_and_etf_sources(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "current-catalog.sqlite"))
    apply_data_policy(store, {
        "REALTIME_QUOTE": {
            "STOCK": ["akshare", "efinance"],
            "ETF": ["akshare", "efinance"],
        },
    })
    catalog = ThesisLedgerProviderRuntime(store).market_route_catalog_v3()
    assert catalog["integrity"] == "complete"
    states = {
        (row["key"]["assetType"], row["target"]["providerId"],
         row["target"]["upstreamSource"]): row["state"]
        for row in catalog["entries"]
        if row["key"]["kind"] == "data"
        and row["key"]["capability"] == "REALTIME_QUOTE"
    }
    assert states[("STOCK", "akshare", "eastmoney")] == "ready"
    assert states[("STOCK", "efinance", "eastmoney")] == "ready"
    assert states[("ETF", "efinance", "eastmoney")] == "ready"
    assert ("ETF", "akshare", "eastmoney") not in states
    assert current_data_adapter_revisions(
        {"kind": "data", "market": "CN", "assetType": "ETF",
         "capability": "REALTIME_QUOTE"},
        {"providerId": "akshare", "upstreamSource": "eastmoney"},
    ) is None


def test_quote_revocation_during_source_call_rejects_result(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "late-quote.sqlite"))
    apply_data_policy(store, {"REALTIME_QUOTE": {"STOCK": ["akshare"]}})
    key = {"kind": "data", "market": "CN", "assetType": "STOCK",
           "capability": "REALTIME_QUOTE"}
    target = {"providerId": "akshare", "upstreamSource": "eastmoney"}

    class Adapter:
        calls = 0

        def get_realtime_quote(self, _symbol, *, source=None):
            self.calls += 1
            assert source == "em"
            store.revoke_route_admission_v3(key=key, target=target, reason="test")
            return {"price": 10, "open": 10, "high": 10, "low": 10,
                    "pre_close": 10, "volume": 1, "amount": 10}

    adapter = Adapter()
    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": adapter})
    with pytest.raises(ThesisLedgerGatewayError) as rejected:
        runtime.execute_request(ThesisLedgerDataRequest("REALTIME_QUOTE", "600519.SH"))
    assert rejected.value.code == "not_admitted"
    assert adapter.calls == 1


def test_nav_history_rejects_rows_outside_admitted_dates(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / "nav-scope.sqlite"))
    apply_data_policy(store, {"FUND_NAV_HISTORY": {"MUTUAL_FUND": ["akshare"]}})
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    frame = pd.DataFrame({"日期": [(today - timedelta(days=1)).isoformat()], "单位净值": [1.1]})

    class Adapter:
        def get_fund_nav_history(self, _symbol):
            return frame

    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": Adapter()})
    runtime._current_market_v3_admission = lambda _key, _target: {
        "admissionState": "admitted",
        "scopeSymbols": ["000001.OF"],
        "scopeDateFrom": today.isoformat(),
        "scopeDateTo": today.isoformat(),
    }
    with pytest.raises(ThesisLedgerGatewayError) as rejected:
        runtime.execute_request(ThesisLedgerDataRequest("FUND_NAV_HISTORY", "000001.OF"))
    assert rejected.value.code == "not_admitted"


@pytest.mark.parametrize("capability", ["FUND_NAV", "FUND_NAV_HISTORY"])
def test_nav_selects_admitted_rows_from_complete_provider_history(tmp_path, capability):
    store = ThesisLedgerControlStore(str(tmp_path / "nav-window.sqlite"))
    apply_data_policy(store, {capability: {"MUTUAL_FUND": ["akshare"]}})
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    frame = pd.DataFrame({
        "日期": [(today - timedelta(days=1)).isoformat(), today.isoformat()],
        "单位净值": [1.0, 1.1],
    })

    class Adapter:
        def get_fund_nav_history(self, _symbol):
            return frame

    runtime = ThesisLedgerProviderRuntime(store, adapters={"akshare": Adapter()})
    runtime._current_market_v3_admission = lambda _key, _target: {
        "admissionState": "admitted",
        "scopeSymbols": ["000001.OF"],
        "scopeDateFrom": today.isoformat(),
        "scopeDateTo": today.isoformat(),
    }
    kwargs = {"start": today.isoformat(), "end": today.isoformat()} if capability == "FUND_NAV_HISTORY" else {}
    execution = runtime.execute_request(ThesisLedgerDataRequest(capability, "000001.OF", **kwargs))
    assert execution.value["日期"].tolist() == [today.isoformat()]
