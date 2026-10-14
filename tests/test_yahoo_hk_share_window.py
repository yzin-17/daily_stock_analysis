"""真实港股切片的逐价网格、零量观察、当前整手及范围拒绝。"""

from datetime import datetime, timezone
import json

import pytest

from data_provider.exact_daily_contract import ExactDailyError
from data_provider.yahoo_hk_share_window import PACK_PATH, SOURCE, load_share_window, price_on_share_grid, read_yahoo_hk_share_window, share_window_instrument_fact
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest
from src.services.thesis_ledger_zero_volume_observations import executable_research_frame


def request(**values):
    return ThesisLedgerDataRequest("DAILY_BAR", values.get("symbol", "00147.HK"),
        instrument_type=values.get("instrument_type", "STOCK"), timeframe="1d",
        adjustment=values.get("adjustment", "none"), start=values.get("start", "2025-08-06"), end="2025-08-11")


def test_real_prices_use_both_spread_bands_and_preserve_zero_observation():
    frame = read_yahoo_hk_share_window(request())
    assert frame.close.tolist() == [0.203, 0.265, 0.25, 0.305]
    assert frame.low.tolist() == [0.182, 0.265, 0.24, 0.25]
    assert frame.volume.tolist() == [81970000, 0, 96859170, 91135000]
    assert frame.amount.isna().all()
    assert frame.attrs["responseSha256"] == "926d9b9a9da30c0fb31babbc5cf22aebc8a75f1a50ec56a1e83252ef753096b7"
    wire = {"symbol": "00147.HK", "tradabilityMode": "assume-untradable-no-bar",
            "routeKey": {"kind": "bar", "capability": "DAILY_BAR", "market": "HK", "assetType": "STOCK", "timeframe": "1d", "adjustment": "none"},
            "routeTarget": {"providerId": "yfinance", "upstreamSource": SOURCE}}
    executable = executable_research_frame(wire, frame)
    assert len(executable) == 3
    excluded = executable.attrs["excluded_zero_volume_observations"]["2025-08-07"]
    assert excluded["volume"] == "0" and excluded["sourceResponseSha256"] == frame.attrs["responseSha256"]
    assert len(executable_research_frame({**wire, "symbol": "00700.HK"}, frame)) == 4


@pytest.mark.parametrize("values", [{"symbol": "00700.HK"}, {"start": "2025-08-05"},
                                   {"adjustment": "qfq"}, {"instrument_type": "ETF"}])
def test_price_scope_is_independent_of_lot_preset(values):
    with pytest.raises(ExactDailyError):
        read_yahoo_hk_share_window(request(**values))


@pytest.mark.parametrize("value", [None, True, float("inf"), float("nan"), -1, 0.303, 0.182001, 0.51])
def test_grid_does_not_round_genuinely_incompatible_prices(value):
    with pytest.raises(ExactDailyError):
        price_on_share_grid(value)


def test_current_lot_does_not_become_historical_rule():
    observed = datetime(2026, 10, 8, 9, tzinfo=timezone.utc)
    fact = share_window_instrument_fact("00147.HK", "2025-08-06", "2025-08-11", observed)
    assert fact["lotSize"] == "5000" and fact["tickSize"] == "0.001"
    assert fact["occurredAt"] == fact["availableAt"] == "2026-10-08T08:57:12.937458+00:00"
    assert fact["executionRules"]["status"] == "unavailable"
    assert "未核验历史整手变更" in fact["executionRules"]["reason"]
    with pytest.raises(ExactDailyError):
        share_window_instrument_fact("00147.HK", "2025-08-06", "2025-08-11", datetime(2026, 10, 8, tzinfo=timezone.utc))


def test_raw_digest_and_historical_qualification_cannot_be_replaced(tmp_path):
    pack = json.loads(PACK_PATH.read_text())
    path = tmp_path / PACK_PATH.name
    path.write_text(json.dumps(pack))
    path.with_name(pack["rawFile"]).write_bytes(PACK_PATH.with_name(pack["rawFile"]).read_bytes().replace(b'"HKD"', b'"USD"'))
    with pytest.raises(ExactDailyError):
        load_share_window(path)
    path.with_name(pack["rawFile"]).write_bytes(PACK_PATH.with_name(pack["rawFile"]).read_bytes())
    pack["lotReference"]["historicalChanges"] = "verified"
    path.write_text(json.dumps(pack))
    with pytest.raises(ExactDailyError):
        load_share_window(path)
