"""真实保存窗口的范围、数字表示、单位时钟及旧路由边界。"""

from datetime import datetime, timezone
import json

import pytest

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.yahoo_share_window import PACK_PATH, SOURCE, load_share_window, price_on_share_grid, read_yahoo_share_window, share_window_instrument_fact
from src.services.thesis_ledger_dependency_facts import DependencyFactError, instrument_facts_response
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime
from src.services.thesis_ledger_native_daily import native_daily_route_matches
from src.services.thesis_ledger_reference_identity import validate_instrument_request
from tests.test_configured_market_routes import store, apply, no_admissions


def request(**values):
    return ThesisLedgerDataRequest("DAILY_BAR", values.get("symbol", "GME.US"),
        instrument_type=values.get("instrument_type", "STOCK"), timeframe="1d",
        adjustment=values.get("adjustment", "none"), start=values.get("start", "2024-05-10"), end=values.get("end", "2024-05-24"))


def test_saved_original_prices_and_market_amount_remain_distinct():
    frame = read_yahoo_share_window(request())
    assert len(frame) == 11
    assert frame.loc[frame.date == "2024-05-14", "open"].iloc[0] == 64.83
    assert frame.loc[frame.date == "2024-05-16", "close"].iloc[0] == 27.67
    assert frame.amount.isna().all() and frame.volume.gt(0).all()
    assert frame.attrs["responseSha256"] == "6bcb64233cc1a17afe6ef212f4803f71a4f2c250652ec91d15c27b64be8df989"
    assert frame.attrs["shareWindowObservedAt"] == "2026-10-08T05:00:13.996180+00:00"


@pytest.mark.parametrize("values", [{"symbol": "AAPL.US"}, {"start": "2024-05-09"}, {"end": "2024-05-27"},
                                   {"adjustment": "qfq"}, {"instrument_type": "ETF"}])
def test_qualification_never_expands_outside_fixed_input(values):
    with pytest.raises(ExactDailyError):
        read_yahoo_share_window(request(**values))


@pytest.mark.parametrize("value", [None, True, float("inf"), float("nan"), -1, 27.675, 27.67002])
def test_grid_rejects_real_price_changes_and_invalid_values(value):
    with pytest.raises(ExactDailyError):
        price_on_share_grid(value)


def test_original_bytes_tampering_is_rejected(tmp_path):
    pack = json.loads(PACK_PATH.read_text())
    path = tmp_path / PACK_PATH.name
    path.write_text(json.dumps(pack))
    path.with_name(pack["rawFile"]).write_bytes(PACK_PATH.with_name(pack["rawFile"]).read_bytes().replace(b'"GME"', b'"XYZ"'))
    with pytest.raises(ExactDailyError):
        load_share_window(path)


def test_units_are_bound_to_actual_observation_range_and_source():
    now = datetime(2026, 10, 8, 6, tzinfo=timezone.utc)
    fact = share_window_instrument_fact("GME.US", "2024-05-10", "2024-05-24", now)
    assert fact["lotSize"] == "1" and fact["tickSize"] == "0.01" and fact["tradable"] is False
    assert validate_instrument_request("GME.US", "US", "STOCK", lambda value: value, True, "none", "yfinance", SOURCE) == "GME.US"
    with pytest.raises(DependencyFactError):
        validate_instrument_request("GME.US", "US", "STOCK", lambda value: value, True, "none")
    with pytest.raises(ExactDailyError):
        share_window_instrument_fact("GME.US", "2024-05-10", "2024-05-24", datetime(2026, 10, 8, tzinfo=timezone.utc))
    parameters = dict(symbol="GME.US", data_as_of=now, start="2024-05-10", end="2024-05-24",
                      execution_start="2024-05-14", execution_end="2024-05-24", fixture_facts=[], instrument_type="STOCK",
                      market="US", identity_only=True,
                      route_key={"kind": "bar", "market": "US", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "none"})
    result = instrument_facts_response(**parameters, route_target={"providerId": "yfinance", "upstreamSource": SOURCE})
    assert result["facts"][0] == fact
    with pytest.raises(DependencyFactError):
        instrument_facts_response(**parameters, route_target={"providerId": "yfinance", "upstreamSource": "yfinance"})


def test_exact_inventory_and_production_runtime_preserve_qfq_boundary(store):
    key = {"kind": "bar", "market": "US", "assetType": "STOCK", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "none"}
    target = {"providerId": "yfinance", "upstreamSource": SOURCE}
    assert not native_daily_route_matches(key, {**target, "upstreamSource": "yfinance"})
    assert not native_daily_route_matches({**key, "market": "HK"}, target)
    assert apply(store, key, target)["effective"]["routes"][0]["targets"][0]["eligible"]
    result = ThesisLedgerProviderRuntime(store, adapters={"yfinance": object()}).execute_market_bars_v3(request(), key)
    assert len(result.value) == 11 and result.value.amount.isna().all()
    no_admissions(store)
