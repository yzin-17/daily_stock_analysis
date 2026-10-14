"""风险与价格限制来源实际读取，保留旧停复牌修订。"""

import json
from datetime import datetime, timezone

import pytest

from src.services.thesis_ledger_trading_status import (
    OfficialTradingStatusProvider, TradingStatusSourceError,
    RISK_DATA_PATH, RISK_UPSTREAM_SOURCE, ROUTE_KEY, PROVIDER_ID,
    build_trading_status_manifest, trading_status_source_ready,
)
from src.services.thesis_ledger_current_data_route import current_data_adapter_revisions
from tests.test_thesis_ledger_trading_status import request as old_request


def request():
    value = old_request()
    value["routeTarget"]["upstreamSource"] = RISK_UPSTREAM_SOURCE
    return value


def test_actual_notice_dimensions_do_not_change_unknown_trading_days():
    response = OfficialTradingStatusProvider().read(request())
    by_day = {item["date"]: item for item in response["days"]}
    assert by_day["2023-08-16"]["state"] == "unknown"
    assert by_day["2023-08-16"]["riskWarning"]["delisting"] == "active"
    assert by_day["2023-08-16"]["riskWarning"]["other"] == "unknown"
    assert by_day["2023-08-16"]["dailyPriceLimit"]["maxUpRatio"] == "0.05"
    assert by_day["2023-08-18"]["riskWarning"] == {"delisting": "none", "other": "none"}
    assert by_day["2023-08-18"]["dailyPriceLimit"]["maxDownRatio"] == "0.1"
    assert by_day["2023-08-21"]["state"] == "unknown"
    assert response["provenance"]["upstreamSource"] == RISK_UPSTREAM_SOURCE
    assert response["sourceDocuments"][0]["sha256"] == "d98685515a9022db66f689cfbe782991575f3de03b0fc7d70c340170ba9b73a2"
    assert response["sourceDocuments"][0]["observedAt"] == "2026-10-07T06:18:28.685988+00:00"
    assert response["provenance"]["historicalAvailability"] == "notVerified"


def test_exact_inventory_and_current_source_readiness():
    target = {"providerId": PROVIDER_ID, "upstreamSource": RISK_UPSTREAM_SOURCE}
    assert current_data_adapter_revisions(ROUTE_KEY, target) is not None
    assert trading_status_source_ready(target)
    manifest = build_trading_status_manifest()
    assert {item["sourceId"] for item in manifest["upstreamSources"]} == {
        "cn-stock-daily-suspension", RISK_UPSTREAM_SOURCE,
        "cn-stock-initial-listing", "cn-stock-delisting-period",
    }
    assert any(item["sourceId"] == RISK_UPSTREAM_SOURCE for item in manifest["upstreamSources"])


def test_old_response_identity_is_still_exact_shared_fixture():
    from tests.test_thesis_ledger_trading_status import test_shared_wire_is_produced_from_actual_package

    test_shared_wire_is_produced_from_actual_package()
    old = OfficialTradingStatusProvider().read(old_request())
    assert all("riskWarning" not in item and "dailyPriceLimit" not in item for item in old["days"])


@pytest.mark.parametrize("field,value", [
    ("riskWarning", {"delisting": "normal", "other": "none"}),
    ("riskWarning", {"delisting": "unknown", "other": "unknown"}),
    ("dailyPriceLimit", {"reference": "adjustedClose", "maxUpRatio": "0.05", "maxDownRatio": "0.05"}),
    ("dailyPriceLimit", {"reference": "previousRawClose", "maxUpRatio": "0", "maxDownRatio": "0.05"}),
    ("dailyPriceLimit", {"reference": "previousRawClose", "maxUpRatio": "NaN", "maxDownRatio": "0.05"}),
    ("dailyPriceLimit", {"reference": "previousRawClose", "maxUpRatio": 0.05, "maxDownRatio": "0.05"}),
])
def test_invalid_dimensions_fail_closed(tmp_path, field, value):
    pack = json.loads(RISK_DATA_PATH.read_text())
    pack["days"][0][field] = value
    path = tmp_path / "risk.json"
    path.write_text(json.dumps(pack))
    with pytest.raises(TradingStatusSourceError) as error:
        OfficialTradingStatusProvider(path).read(request())
    assert error.value.code == "invalid_response"


def test_no_future_state_or_other_symbol_is_added():
    value = request()
    value["range"] = {"start": "2023-08-16", "end": "2023-08-16"}
    single = OfficialTradingStatusProvider().read(value)
    assert len(single["days"]) == 1
    assert single["days"][0]["dailyPriceLimit"]["maxUpRatio"] == "0.05"
    value["symbol"] = "600000.SH"
    with pytest.raises(TradingStatusSourceError) as error:
        OfficialTradingStatusProvider().read(value)
    assert error.value.code == "not_covered"


def test_source_observation_cannot_be_claimed_before_the_actual_read():
    with pytest.raises(TradingStatusSourceError) as error:
        OfficialTradingStatusProvider(clock=lambda: datetime(2026, 10, 6, tzinfo=timezone.utc)).read(request())
    assert error.value.code == "invalid_response"
