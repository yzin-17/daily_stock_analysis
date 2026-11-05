"""ETF精确来源、标准合同和真实本地资料通过认证HTTP的闭环。"""

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from importlib import import_module
from types import MappingProxyType

import pytest

from src.services.thesis_ledger_etf_fee_sources import SOURCES, BASE
from src.services.thesis_ledger_multimarket_rules import MultimarketRuleProvider
from src.services.thesis_ledger_public_fees import OfficialPublicFeeProvider, PublicFeeSourceError
from src.services.thesis_ledger_public_fees_contract_v1 import FeeRequest, FeeResult
from src.services.thesis_ledger_capability_registry import ExactProbeReadContext
from tests import test_thesis_ledger_public_fee_routes as routes

configured_client = routes.configured_client


def request(market):
    provider = "official-rules" if market == "CN" else f"official-{market.lower()}-rules"
    _, source, _, filename = SOURCES[provider]
    pack = json.loads((BASE / filename).read_text())
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "etf-fees",
        "routeKey": {"kind": "data", "market": market, "assetType": "ETF", "capability": "HISTORICAL_PUBLIC_FEES"},
        "routeTarget": {"providerId": provider, "upstreamSource": source, "routeIndex": 0},
        "symbol": pack["symbol"], "range": pack["coverage"]}


def provider(market):
    kind = OfficialPublicFeeProvider if market == "CN" else MultimarketRuleProvider
    return kind(clock=lambda: datetime(2026, 11, 6, tzinfo=timezone.utc))


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_exact_probe_consumes_immutable_registry_context(monkeypatch, market):
    wire = request(market)
    identity = wire["routeTarget"]["providerId"]
    module = import_module("data_provider.thesis_ledger_sources." + identity.replace("-", "_"))
    provider_name = "OfficialPublicFeeProvider" if market == "CN" else "MultimarketRuleProvider"
    kind = getattr(module, provider_name)
    monkeypatch.setattr(module, provider_name,
        lambda *args: kind(*args, clock=lambda: datetime(2026, 11, 6, tzinfo=timezone.utc)))
    target = {key: value for key, value in wire["routeTarget"].items() if key != "routeIndex"}
    context = ExactProbeReadContext(MappingProxyType(wire["routeKey"]), MappingProxyType(target), None)
    sample = module.SOURCE.exact_probe_read(context)
    assert sample == {"symbol": wire["symbol"], **wire["range"]}


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_real_pack_contract_and_fee_dimensions(market):
    wire = request(market)
    path = BASE / SOURCES[wire["routeTarget"]["providerId"]][3]
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = provider(market).read(FeeRequest.model_validate(wire).model_dump())
    parsed = FeeResult.model_validate(result)
    assert parsed.routeKey.assetType == "ETF"
    assert parsed.provenance.historicalAvailability == "notVerified"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    fees = {fee["code"]: fee for fee in result["segments"][0]["charges"]}
    if market == "CN":
        assert set(fees) == {"handlingFee"}
        assert fees["handlingFee"]["rate"] == "0.00004"
        assert "非完整账单" in result["provenance"]["coverageDescription"]
    elif market == "HK":
        assert fees["stampDuty"]["rate"] == "0"
        assert fees["stampDuty"]["sourceDocumentIds"] == ["hkex-etf-waiver-2015"]
        assert set(fees) == {"handlingFee", "stampDuty", "regulatoryFee", "afrcLevy"}
    else:
        assert fees["tradingActivityFee"]["basis"] == "actualShares"
        assert "finra-taf-etf-faq" in fees["tradingActivityFee"]["sourceDocumentIds"]
        assert result["segments"][1]["range"]["start"] == "2024-05-20"


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
@pytest.mark.parametrize("change,code", [("asset", "unsupported_capability"),
    ("symbol", "not_covered"), ("range", "insufficient_coverage")])
def test_identity_or_window_cannot_borrow_etf_facts(market, change, code):
    wire = deepcopy(request(market))
    if change == "asset":
        wire["routeKey"]["assetType"] = "STOCK"
    elif change == "symbol":
        wire["symbol"] = "OTHER.US"
    else:
        wire["range"]["start"] = "2020-01-01"
    with pytest.raises(PublicFeeSourceError) as error:
        provider(market).read(wire)
    assert error.value.code == code


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_catalog_probe_policy_and_authenticated_gateway(configured_client, market):
    client, _, _ = configured_client
    wire = request(market)
    target = {key: value for key, value in wire["routeTarget"].items() if key != "routeIndex"}
    catalog = client.get("/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3",
        headers=routes.CONTROL_HEADERS)
    assert catalog.status_code == 200
    entry = next(row for row in catalog.json()["entries"] if row["key"] == wire["routeKey"] and row["target"] == target)
    assert entry["state"] == "ready"
    policy = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "etf-fee-policy",
        "revision": 1, "enabled": True, "routes": [{"key": wire["routeKey"], "targets": [target]}]}
    assert client.post("/api/v3/thesis-ledger/control/policies/apply", json=policy, headers=routes.CONTROL_HEADERS).status_code == 200
    assert client.post(routes.DATA_URL, json=wire, headers={}).status_code == 401
    result = client.post(routes.DATA_URL, json=wire, headers=routes.DATA_HEADERS)
    assert result.status_code == 200
    assert FeeResult.model_validate(result.json()).routeKey.assetType == "ETF"
