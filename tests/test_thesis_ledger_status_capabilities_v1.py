"""五种独立能力的共享 wire 样例和真实 registry 边界；不会注册生产来源。"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_status_capabilities_v1 import (
    REQUESTS, RESULTS, TRADING_CONTRACT, RISK_CONTRACT, PRICE_CONTRACT, DATES_CONTRACT, PHASE_CONTRACT, correlate,
)

PATH = Path(__file__).parent / "fixtures/market-status-capabilities-v1.shared.json"
FIXTURES = json.loads(PATH.read_text())["valid"]
CONTRACTS = {item.capability: item for item in (TRADING_CONTRACT, RISK_CONTRACT, PRICE_CONTRACT, DATES_CONTRACT, PHASE_CONTRACT)}


def fixture(capability="DAILY_TRADING_STATUS"):
    return deepcopy(next(item for item in FIXTURES if item["name"] == capability))


def test_cross_repository_wire_fixture_is_identical_when_checkout_exists():
    shared = Path(__file__).resolve().parents[2] / "thesis-ledger/packages/schemas/test/fixtures/market-status-capabilities-v1.shared.json"
    if shared.exists():
        assert hashlib.sha256(shared.read_bytes()).digest() == hashlib.sha256(PATH.read_bytes()).digest()


@pytest.mark.parametrize("item", FIXTURES, ids=lambda item: item["name"])
def test_typed_contract_really_executes_through_registry(item):
    calls = []
    contract = CONTRACTS[item["name"]]

    def read(request, context):
        calls.append(request)
        return RESULTS.validate_python(item["result"])

    source = SourceDefinition("fixture-status", lambda: {"providerId": "fixture-status"}, lambda context: context.adapter, (
        QueryBinding(contract, ExactKey("CN", "STOCK", item["name"]), "fixture-upstream", "fixture-adapter-v1", "fixture-source-v1", read),
    ))
    registry = CapabilityRegistry((source,))
    result = registry.query(contract, item["request"], item["request"]["routeKey"],
                            item["request"]["routeTarget"], ReadContext(None, None))
    assert result.model_dump(exclude_unset=True) == item["result"]
    assert len(calls) == 1


@pytest.mark.parametrize("field", ["requestId", "symbol", "policyRevision", "target", "index", "range"])
def test_response_correlation(field):
    item = fixture()
    value = item["result"]
    if field in {"requestId", "symbol"}:
        value[field] = "different-request" if field == "requestId" else "000995.SZ"
    elif field == "policyRevision":
        value[field] += 1
    elif field == "target":
        value["routeTarget"]["providerId"] = "different-provider"
    elif field == "index":
        value["routeTarget"]["routeIndex"] = 1
    else:
        value["range"]["start"] = value["coverage"]["range"]["start"] = "2024-07-14"
    with pytest.raises(ValueError):
        correlate(REQUESTS.validate_python(item["request"]), RESULTS.validate_python(value), item["request"]["routeTarget"])


@pytest.mark.parametrize("field", ["unknown", "missing", "unproved-calendar", "future-clock", "future-availability", "wrong-timezone", "duplicate", "out-of-range"])
def test_false_complete_coverage(field):
    value = fixture()["result"]
    if field == "unknown":
        value["days"][0]["state"] = "unknown"
    elif field == "missing":
        value["days"] = []
    elif field == "unproved-calendar":
        value["coverage"]["calendarEvidenceRefs"] = []
    elif field in {"future-clock", "future-availability"}:
        value["evidence"][0]["observedAt" if field == "future-clock" else "availableAt"] = "2025-01-01T00:00:00Z"
    elif field == "wrong-timezone":
        value["clock"]["timezone"] = "America/New_York"
    elif field == "duplicate":
        value["days"].append(value["days"][0])
    else:
        value["days"][0]["date"] = "2024-07-14"
    with pytest.raises(ValidationError):
        RESULTS.validate_python(value)


def test_coarse_st_does_not_prove_precise_risk():
    value = fixture("RISK_WARNING")["result"]
    RESULTS.validate_python(value)
    value["days"][0]["delisting"] = "none"
    with pytest.raises(ValidationError):
        RESULTS.validate_python(value)
    value["days"][0]["evidenceRefs"] = ["document"]
    RESULTS.validate_python(value)


def test_unknown_remains_unknown_and_incomplete():
    value = fixture()["result"]
    value["days"][0].update(state="unknown", evidenceRefs=[])
    value["coverage"].update(unknownDates=["2024-07-15"], complete=False)
    RESULTS.validate_python(value)


def test_exact_decimal_limits_and_ratio_are_not_float_guesses():
    value = fixture("DAILY_PRICE_RULE")["result"]
    value["days"][0]["rule"]["maxUpRatio"] = "0.999999999999999999999999"
    RESULTS.validate_python(value)
    value["days"][0]["rule"] = {"kind": "exact", "currency": "CNY", "lower": "1.000000000000000002", "upper": "1.000000000000000001"}
    with pytest.raises(ValidationError):
        RESULTS.validate_python(value)
    value["days"][0]["rule"]["lower"] = "1.000000000000000001"
    RESULTS.validate_python(value)


def test_coarse_risk_and_ratio_do_not_satisfy_precise_requirements():
    for name, field, value in (("RISK_WARNING", "detail", {"kind": "riskCategories", "categories": ["delisting", "other"]}), ("DAILY_PRICE_RULE", "representation", "exactPrices")):
        result = fixture(name)["result"]
        result[field] = value
        with pytest.raises(ValidationError):
            RESULTS.validate_python(result)
        result["coverage"].update(unknownDates=["2024-07-15"], complete=False)
        RESULTS.validate_python(result)


def test_basic_dates_do_not_accept_price_window_or_last_trading_day():
    item = fixture("SECURITY_BASIC_DATES")
    with pytest.raises(ValidationError):
        REQUESTS.validate_python({**item["request"], "range": {"start": "2024-01-01", "end": "2024-01-02"}})
    item["result"]["dates"]["lastTradingDate"] = "2024-07-15"
    with pytest.raises(ValidationError):
        RESULTS.validate_python(item["result"])


@pytest.mark.parametrize("field", ["target", "market", "capability", "boolean-version"])
def test_wrong_exact_request_rejected_before_provider_call(field):
    item = fixture()
    calls = []
    source = SourceDefinition("fixture-status", lambda: {}, lambda context: context.adapter, (
        QueryBinding(TRADING_CONTRACT, ExactKey("CN", "STOCK", "DAILY_TRADING_STATUS"), "fixture-upstream",
                     "fixture-adapter-v1", "fixture-source-v1", lambda request, context: calls.append(request)),
    ))
    key, target = deepcopy(item["request"]["routeKey"]), deepcopy(item["request"]["routeTarget"])
    if field == "target":
        item["request"]["routeTarget"]["providerId"] = "different-provider"
    elif field == "market":
        item["request"]["symbol"] = "GME.US"
    elif field == "capability":
        item["request"]["routeKey"]["capability"] = "RISK_WARNING"
    else:
        item["request"]["capabilityVersion"] = True
    with pytest.raises(CapabilityContractError, match="请求未通过"):
        CapabilityRegistry((source,)).query(TRADING_CONTRACT, item["request"], key, target, ReadContext(None, None))
    assert not calls
