"""公共收费生产规则包的区间读取与 wire 对账。"""

import json
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.services.thesis_ledger_public_fees import (
    CAPABILITY,
    DATA_PATH,
    PROVIDER_ID,
    UPSTREAM_SOURCE,
    OfficialPublicFeeProvider,
    PublicFeeSourceError,
)


def request(start="2023-08-25", end="2023-08-29"):
    return {
        "contractVersion": 3, "consumer": "thesis-ledger",
        "requestId": "public-fees-boundary-sample",
        "routeKey": {"kind": "data", "market": "CN", "assetType": "STOCK", "capability": CAPABILITY},
        "routeTarget": {"providerId": PROVIDER_ID, "upstreamSource": UPSTREAM_SOURCE, "routeIndex": 0},
        "symbol": "600000.SH", "range": {"start": start, "end": end},
    }


def test_production_pack_returns_fee_change_and_source_types():
    result = OfficialPublicFeeProvider().read(request())
    assert [segment["range"] for segment in result["segments"]] == [
        {"start": "2023-08-25", "endExclusive": "2023-08-28"},
        {"start": "2023-08-28", "endExclusive": "2023-08-30"},
    ]
    before, after = [dict((fee["code"], fee["rate"]) for fee in item["charges"]) for item in result["segments"]]
    assert Decimal(before["stampDuty"]) * 10000 == Decimal("10")
    assert Decimal(after["stampDuty"]) * 10000 == Decimal("5")
    assert before["handlingFee"] == "0.0000487"
    assert after["handlingFee"] == "0.0000341"
    assert before["regulatoryFee"] == after["regulatoryFee"] == "0.00002"
    assert before["transferFee"] == after["transferFee"] == "0.00001"
    assert result["provenance"]["calculationSettings"] == "executionModel"
    assert result["provenance"]["historicalAvailability"] == "notVerified"
    assert next(item for item in result["sourceDocuments"] if item["id"] == "transfer-2022-republication")["kind"] == "governmentNewsRepublication"


def test_single_day_is_clipped_without_previous_or_future_segment():
    result = OfficialPublicFeeProvider().read(request("2023-08-28", "2023-08-28"))
    assert len(result["segments"]) == 1
    assert result["segments"][0]["range"] == {"start": "2023-08-28", "endExclusive": "2023-08-29"}


def test_real_scenario_window_keeps_old_rate_before_first_sell():
    result = OfficialPublicFeeProvider().read(request("2023-08-23", "2023-08-29"))
    assert result["segments"][0]["range"] == {"start": "2023-08-23", "endExclusive": "2023-08-28"}
    assert next(fee for fee in result["segments"][0]["charges"] if fee["code"] == "stampDuty")["rate"] == "0.001"


@pytest.mark.parametrize("start,end", [("2023-08-22", "2023-08-29"), ("2023-08-25", "2023-08-30")])
def test_outside_actual_pack_coverage_is_not_filled(start, end):
    with pytest.raises(PublicFeeSourceError) as caught:
        OfficialPublicFeeProvider().read(request(start, end))
    assert caught.value.code == "insufficient_coverage"


@pytest.mark.parametrize("change,code", [
    ({"symbol": "600000.SZ"}, "not_covered"),
    ({"routeKey": {"kind": "data", "market": "HK", "assetType": "STOCK", "capability": CAPABILITY}}, "unsupported_capability"),
    ({"range": {"start": "2023-08-29", "end": "2023-08-25"}}, "invalid_request"),
])
def test_wrong_identity_or_reversed_dates_are_explicit(change, code):
    with pytest.raises(PublicFeeSourceError) as caught:
        OfficialPublicFeeProvider().read({**request(), **change})
    assert caught.value.code == code


def test_source_is_offline_and_revision_does_not_include_fetch_clock(monkeypatch):
    import urllib.request

    def fail_network(*args, **kwargs):
        raise AssertionError("公共收费本地读取不得联网")

    monkeypatch.setattr(urllib.request, "urlopen", fail_network)
    first = OfficialPublicFeeProvider(clock=lambda: datetime(2026, 10, 6, tzinfo=timezone.utc)).read(request())
    second = OfficialPublicFeeProvider(clock=lambda: datetime(2026, 10, 7, tzinfo=timezone.utc)).read(request())
    assert first["provenance"]["fetchedAt"] != second["provenance"]["fetchedAt"]
    assert first["provenance"]["providerRevision"] == second["provenance"]["providerRevision"]


@pytest.mark.parametrize("mutation,expected_code", [
    ("missingFee", "invalid_response"),
    ("negativeRate", "invalid_response"),
    ("malformedPack", "invalid_response"),
    ("gap", "insufficient_coverage"),
])
def test_unusable_calculation_data_is_reported_once(tmp_path, mutation, expected_code):
    pack = json.loads(DATA_PATH.read_text())
    if mutation == "missingFee":
        pack["segments"][0]["charges"].pop()
    elif mutation == "negativeRate":
        pack["segments"][0]["charges"][0]["rate"] = "-0.001"
    elif mutation == "malformedPack":
        pack = {}
    else:
        pack["segments"][1]["range"]["start"] = "2023-08-29"
    path = tmp_path / "fees.json"
    path.write_text(json.dumps(pack))
    with pytest.raises(PublicFeeSourceError) as caught:
        OfficialPublicFeeProvider(path).read(request())
    assert caught.value.code == expected_code


def test_wire_fixture_is_generated_from_production_pack():
    path = Path(__file__).resolve().parents[2] / "thesis-ledger/packages/schemas/fixtures/market-public-fees-v3.response.json"
    if not path.exists():
        pytest.skip("独立 DSA checkout 未包含共享 Schema fixture")
    fixture = json.loads(path.read_text())
    provider = OfficialPublicFeeProvider(clock=lambda: datetime(2026, 10, 6, tzinfo=timezone.utc))
    assert provider.read(deepcopy(fixture["request"])) == fixture["response"]
