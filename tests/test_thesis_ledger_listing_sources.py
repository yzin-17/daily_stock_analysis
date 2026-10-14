"""实际上市与退市包的生产读取及来源范围拒绝。"""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from src.services.thesis_ledger_trading_status import (
    LIFECYCLE_PATHS, PROVIDER_ID, ROUTE_KEY, OfficialTradingStatusProvider, TradingStatusSourceError,
    build_trading_status_manifest,
)


def request(source, symbol, start, end):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "listing-test",
            "routeKey": ROUTE_KEY, "routeTarget": {"providerId": PROVIDER_ID, "upstreamSource": source, "routeIndex": 0},
            "symbol": symbol, "range": {"start": start, "end": end}}


def provider(path=None):
    return OfficialTradingStatusProvider(path, clock=lambda: datetime(2026, 10, 8, tzinfo=timezone.utc))


def test_initial_listing_preserves_actual_first_five_sessions_and_next_limit():
    result = provider().read(request("cn-stock-initial-listing", "603350.SH", "2024-07-02", "2024-07-11"))
    assert result["days"][0]["listingPhase"] == "notListed"
    assert [d["date"] for d in result["days"] if "noDailyPriceLimit" in d] == [
        "2024-07-03", "2024-07-04", "2024-07-05", "2024-07-08", "2024-07-09"]
    assert result["days"][-2]["dailyPriceLimit"]["maxUpRatio"] == "0.1"
    assert {d["state"] for d in result["days"]} == {"unknown"}
    assert result["sourceDocuments"][0]["sha256"].startswith("546014a6")


def test_delisting_keeps_actual_suspension_and_exchange_exit_distinct():
    result = provider().read(request("cn-stock-delisting-period", "600462.SH", "2025-07-10", "2025-07-22"))
    by_day = {d["date"]: d for d in result["days"]}
    assert by_day["2025-07-14"]["listingPhase"] == "delistingPeriod"
    assert by_day["2025-07-15"]["state"] == "suspended"
    assert by_day["2025-07-21"]["listingPhase"] == "delisted"
    assert by_day["2025-07-21"]["state"] == "unknown"


@pytest.mark.parametrize("symbol,start,end", [
    ("000995.SZ", "2024-07-03", "2024-07-11"),
    ("603350.SH", "2024-07-01", "2024-07-11"),
    ("603350.SH", "2024-07-03", "2024-07-12"),
])
def test_does_not_expand_to_unknown_identity_or_date(symbol, start, end):
    with pytest.raises(TradingStatusSourceError) as error:
        provider().read(request("cn-stock-initial-listing", symbol, start, end))
    assert error.value.code == "not_covered"


@pytest.mark.parametrize("mutation", ["identity", "contradiction", "missingReference", "futureClock"])
def test_bad_pack_is_rejected(tmp_path, mutation):
    source = "cn-stock-initial-listing"
    pack = deepcopy(json.loads(LIFECYCLE_PATHS[source].read_text()))
    if mutation == "identity":
        pack["upstreamSource"] = "cn-stock-delisting-period"
    elif mutation == "contradiction":
        pack["days"][0]["state"] = "trading"
    elif mutation == "missingReference":
        pack["days"][0]["sourceDocumentIds"] = []
    else:
        pack["sourceDocuments"][0]["observedAt"] = "2026-10-09T00:00:00Z"
    path = tmp_path / "pack.json"
    path.write_text(json.dumps(pack))
    with pytest.raises(TradingStatusSourceError) as error:
        provider(path).read(request(source, "603350.SH", "2024-07-02", "2024-07-11"))
    assert error.value.code == "invalid_response"


def test_manifest_catalogs_exact_symbol_and_window_for_each_source():
    sources = {s["sourceId"]: s for s in build_trading_status_manifest()["upstreamSources"]}
    assert sources["cn-stock-initial-listing"]["localHistory"]["symbols"] == ["603350.SH"]
    assert sources["cn-stock-delisting-period"]["localHistory"]["coverage"]["end"] == "2025-07-22"
