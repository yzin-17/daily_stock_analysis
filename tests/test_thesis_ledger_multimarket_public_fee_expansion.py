"""真实港美扩展费包、控制网关与逐项算法维度拒绝。"""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from src.services.thesis_ledger_multimarket_rules import MultimarketRuleProvider, BASE
from src.services.thesis_ledger_public_fees import PublicFeeSourceError
from tests import test_thesis_ledger_public_fee_routes as routes

configured_client = routes.configured_client


def request(market="HK"):
    symbol, start, end = ("00147.HK", "2025-08-06", "2025-08-11") if market == "HK" else ("GME.US", "2024-05-10", "2024-05-24")
    target = {"providerId": f"official-{market.lower()}-rules", "upstreamSource": f"{market.lower()}-stock-public-fees", "routeIndex": 0}
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "expanded-fees",
            "routeKey": {"kind": "data", "market": market, "assetType": "STOCK", "capability": "HISTORICAL_PUBLIC_FEES"},
            "routeTarget": target, "symbol": symbol, "range": {"start": start, "end": end}}


def provider(path=None):
    return MultimarketRuleProvider(path, clock=lambda: datetime(2026, 10, 8, tzinfo=timezone.utc))


def test_hk_real_four_fees_and_rounding_have_separate_documents():
    result = provider().read(request())
    charges = {charge["code"]: charge for charge in result["segments"][0]["charges"]}
    assert set(charges) == {"handlingFee", "stampDuty", "regulatoryFee", "afrcLevy"}
    assert charges["stampDuty"]["amountRounding"] == {"mode": "ceil", "decimalPlaces": 0}
    assert charges["regulatoryFee"]["rate"] == "0.000027"
    assert charges["afrcLevy"]["rate"] == "0.0000015"
    assert charges["regulatoryFee"]["sourceDocumentIds"] != charges["afrcLevy"]["sourceDocumentIds"]


def test_us_real_taf_basis_cap_and_low_price_exception_keep_section31_boundary():
    result = provider().read(request("US"))
    assert result["segments"][1]["range"]["start"] == "2024-05-20"
    for segment in result["segments"]:
        fee = next(f for f in segment["charges"] if f["code"] == "tradingActivityFee")
        assert (fee["basis"], fee["rate"], fee["maximumAmount"]) == ("actualShares", "0.000166", "8.30")
        assert fee["lowPriceExemption"] == "belowPerShareRate"
    assert next(d for d in result["sourceDocuments"] if d["id"] == "finra-taf-2020-schedule")["publishedDate"] is None


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "basis", "rounding", "badReference", "badRate"])
def test_hk_invalid_or_merged_charges_fail(tmp_path, mutation):
    pack = deepcopy(json.loads((BASE / "hk-00147-public-fees.json").read_text()))
    charges = pack["segments"][0]["charges"]
    if mutation == "missing":
        charges.pop()
    elif mutation == "duplicate":
        charges.append(charges[0])
    elif mutation == "basis":
        charges[-1]["basis"] = "actualShares"
    elif mutation == "rounding":
        charges[1]["amountRounding"] = {"mode": "halfUp", "decimalPlaces": 2}
    elif mutation == "badReference":
        charges[-1]["sourceDocumentIds"] = ["missing"]
    else:
        charges[-1]["rate"] = "NaN"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(pack))
    with pytest.raises(PublicFeeSourceError):
        provider(path).read(request())


@pytest.mark.parametrize("market", ["HK", "US"])
def test_actual_control_and_authenticated_http_read(configured_client, market):
    client, _runtime, _store = configured_client
    wire = request(market)
    target = {k: v for k, v in wire["routeTarget"].items() if k != "routeIndex"}
    policy = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "new-fee-policy", "revision": 1,
              "enabled": True, "routes": [{"key": wire["routeKey"], "targets": [target]}]}
    assert client.post("/api/v3/thesis-ledger/control/policies/apply", json=policy, headers=routes.CONTROL_HEADERS).status_code == 200
    path = "/api/v3/thesis-ledger/market/public-fees"
    assert client.post(path, json=wire, headers={}).status_code == 401
    response = client.post(path, json=wire, headers=routes.DATA_HEADERS)
    assert response.status_code == 200
    assert response.json()["provenance"]["upstreamSource"] == target["upstreamSource"]
