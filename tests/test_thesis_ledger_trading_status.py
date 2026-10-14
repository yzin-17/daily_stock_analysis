"""实际公告生产包与跨语言日级状态合同。"""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from src.services.thesis_ledger_trading_status import (
    DATA_PATH, PROVIDER_ID, ROUTE_KEY, UPSTREAM_SOURCE,
    OfficialTradingStatusProvider, TradingStatusSourceError,
)


def request(start="2023-08-16", end="2023-08-21"):
    return {
        "contractVersion": 3, "consumer": "thesis-ledger", "requestId": "trading-status-source-sample",
        "routeKey": ROUTE_KEY,
        "routeTarget": {"providerId": PROVIDER_ID, "upstreamSource": UPSTREAM_SOURCE, "routeIndex": 0},
        "symbol": "000995.SZ", "range": {"start": start, "end": end},
    }


def test_actual_announcement_only_establishes_two_daily_states():
    result = OfficialTradingStatusProvider().read(request())
    assert [(day["date"], day["state"]) for day in result["days"]] == [
        ("2023-08-16", "unknown"), ("2023-08-17", "suspended"), ("2023-08-18", "trading"),
        ("2023-08-19", "unknown"), ("2023-08-20", "unknown"), ("2023-08-21", "unknown"),
    ]
    assert result["sourceDocuments"][0]["url"].endswith("1217557800.PDF")
    assert "2023-029" in result["sourceDocuments"][0]["title"]
    assert result["provenance"]["historicalAvailability"] == "notVerified"
    assert all("sourceDocumentIds" not in day for day in result["days"] if day["state"] == "unknown")


def test_single_suspended_day_does_not_include_future_resume_date():
    result = OfficialTradingStatusProvider().read(request("2023-08-17", "2023-08-17"))
    assert result["days"] == [{
        "date": "2023-08-17", "state": "suspended", "sourceDocumentIds": ["huangtai-2023-029"],
    }]
    assert "2023-08-18" not in json.dumps(result)


def test_unknown_window_is_not_reported_as_no_suspension_or_trading():
    result = OfficialTradingStatusProvider().read(request("2023-08-19", "2023-08-20"))
    assert {day["state"] for day in result["days"]} == {"unknown"}
    assert result["sourceDocuments"] == []


def test_reads_offline_and_version_is_independent_of_clock(monkeypatch):
    import urllib.request

    def fail_network(*args, **kwargs):
        raise AssertionError("本地状态读取不得联网")

    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    first = OfficialTradingStatusProvider(clock=lambda: datetime(2026, 10, 6, tzinfo=timezone.utc)).read(request())
    second = OfficialTradingStatusProvider(clock=lambda: datetime(2026, 10, 7, tzinfo=timezone.utc)).read(request())
    assert first["provenance"]["providerRevision"] == second["provenance"]["providerRevision"]
    assert first["provenance"]["fetchedAt"] != second["provenance"]["fetchedAt"]


@pytest.mark.parametrize("change,code", [
    ({"symbol": "600000.SH"}, "not_covered"),
    ({"range": {"start": "2023-08-21", "end": "2023-08-16"}}, "invalid_request"),
    ({"routeKey": {**ROUTE_KEY, "market": "HK"}}, "unsupported_capability"),
])
def test_identity_and_window_are_not_silently_replaced(change, code):
    with pytest.raises(TradingStatusSourceError) as caught:
        OfficialTradingStatusProvider().read({**request(), **change})
    assert caught.value.code == code


@pytest.mark.parametrize("mutation", ["duplicateDay", "missingReference", "unknownValue"])
def test_invalid_known_state_is_not_silently_replaced_by_unknown(tmp_path, mutation):
    pack = json.loads(DATA_PATH.read_text())
    if mutation == "duplicateDay":
        pack["days"].append(deepcopy(pack["days"][0]))
    elif mutation == "missingReference":
        pack["days"][0]["sourceDocumentIds"] = []
    else:
        pack["days"][0]["state"] = "maybe"
    path = tmp_path / "states.json"
    path.write_text(json.dumps(pack))
    with pytest.raises(TradingStatusSourceError) as caught:
        OfficialTradingStatusProvider(path).read(request())
    assert caught.value.code == "invalid_response"


def test_shared_wire_is_produced_from_actual_package():
    path = Path(__file__).resolve().parents[2] / "thesis-ledger/packages/schemas/fixtures/market-trading-status-v3.response.json"
    if not path.exists():
        pytest.skip("独立 DSA checkout 未包含共享 Schema fixture")
    sample = json.loads(path.read_text())
    provider = OfficialTradingStatusProvider(clock=lambda: datetime(2026, 10, 6, tzinfo=timezone.utc))
    assert provider.read(sample["request"]) == sample["response"]
