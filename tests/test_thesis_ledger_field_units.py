"""原生单位在完整窗口及图表响应中的身份与数值保真。"""

from copy import deepcopy

import pytest

from api.thesis_ledger import _market_data_v3_response
from api.thesis_ledger_chart_v3 import chart_response
from api.thesis_ledger_source_basis import native_field_units
from data_provider.akshare_daily_contract import annotate_exact_daily_contract
from src.services.thesis_ledger_market_v3_facts import (
    HITHINK_ETF_HISTORY_SOURCE,
    HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1,
    HITHINK_ETF_NATIVE_FIELD_CONTRACT_V1,
    market_coverage_context_v3,
)
from src.services.thesis_ledger_hithink_etf_units import hithink_etf_field_contract
from src.services.thesis_ledger_market_v3_facts import HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1
from tests.test_thesis_ledger_market_v3 import REQUEST, _execution, _target_frame


@pytest.mark.parametrize("kind,expected", [
    ("stock", {"volume": "hand", "amount": "CNY"}),
    ("etf", {"volume": "unknown", "amount": "unknown"}),
])
@pytest.mark.parametrize("adjustment", ["none", "qfq", "hfq"])
def test_native_unit_projection(kind, expected, adjustment):
    frame = annotate_exact_daily_contract(_target_frame(), kind, "eastmoney", adjustment)
    key = {**REQUEST["routeKey"], "assetType": kind.upper(), "adjustment": adjustment}
    assert native_field_units(frame.attrs, key, "akshare", "eastmoney") == {"fieldUnits": expected}


def test_hithink_etf_units_remain_explicitly_unknown_and_identity_bound():
    contract = {
        "contractVersion": HITHINK_ETF_NATIVE_FIELD_CONTRACT_V1,
        "providerId": "hithink",
        "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
        "assetType": "ETF",
        "adjustment": "qfq",
        "volumeUnit": "unknown",
        "amountUnit": "unknown",
        "valuesConverted": False,
    }
    attrs = {"hithink_etf_field_units": contract}
    assert native_field_units(attrs, REQUEST["routeKey"], "hithink", HITHINK_ETF_HISTORY_SOURCE) == {
        "fieldUnits": {"volume": "unknown", "amount": "unknown"}
    }

    for field, value in (("volumeUnit", "fund-unit"), ("amountUnit", "CNY"),
                         ("valuesConverted", True), ("providerId", "akshare")):
        invalid = {"hithink_etf_field_units": {**contract, field: value}}
        with pytest.raises(ValueError):
            native_field_units(invalid, REQUEST["routeKey"], "hithink", HITHINK_ETF_HISTORY_SOURCE)


def test_hithink_explicit_unknown_units_survive_v3_response_and_bind_fingerprint(monkeypatch):
    import api.thesis_ledger as api

    monkeypatch.setattr(api, "_now_iso", lambda: "2026-09-27T00:00:00+00:00")
    request = deepcopy(REQUEST)
    request["routeTarget"] = {
        "providerId": "hithink",
        "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
        "routeIndex": 0,
    }
    frame = _target_frame()
    frame.attrs["upstream_source"] = HITHINK_ETF_HISTORY_SOURCE
    frame.attrs["thesis_ledger_v3_pagination"]["protocol"] = HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1
    execution = _execution(
        frame, provider="hithink", upstream_source=HITHINK_ETF_HISTORY_SOURCE,
    )
    context = market_coverage_context_v3(
        symbol=request["symbol"], market=request["routeKey"]["market"],
        start=request["start"], end=request["end"],
    )
    old = _market_data_v3_response(request, execution, context)
    assert "fieldUnits" not in old["sourcePriceBasis"]

    frame.attrs["hithink_etf_field_units"] = {
        "contractVersion": HITHINK_ETF_NATIVE_FIELD_CONTRACT_V1,
        "providerId": "hithink",
        "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
        "assetType": "ETF",
        "adjustment": "qfq",
        "volumeUnit": "unknown",
        "amountUnit": "unknown",
        "valuesConverted": False,
    }
    current = _market_data_v3_response(request, execution, context)
    assert current["sourcePriceBasis"]["fieldUnits"] == {
        "volume": "unknown", "amount": "unknown",
    }
    assert current["bars"] == old["bars"]
    assert current["inputFingerprint"] != old["inputFingerprint"]


@pytest.mark.parametrize("symbol,start,end,revision", [
    ("510300.SH", "2026-04-30", "2026-08-09", HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1),
    ("159516.SZ", "2026-04-29", "2026-08-09", HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1),
    ("159516.SZ", "2026-04-30", "2026-08-10", HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1),
    ("159516.SZ", "2026-04-30", "2026-08-09", "changed-source-contract"),
])
def test_hithink_observed_units_do_not_escape_reviewed_scope(symbol, start, end, revision):
    contract = hithink_etf_field_contract(
        symbol=symbol, start=start, end=end, source_revision=revision,
    )
    assert (contract["volumeUnit"], contract["amountUnit"]) == ("unknown", "unknown")
    assert "unitEvidenceId" not in contract


@pytest.mark.parametrize("mode", ["window", "chart"])
def test_hithink_observed_units_require_matching_request_and_preserve_bars(monkeypatch, mode):
    import api.thesis_ledger as api
    import api.thesis_ledger_chart_v3 as chart

    for module in (api, chart):
        monkeypatch.setattr(module, "_now_iso", lambda: "2026-09-27T00:00:00+00:00")
    request = deepcopy(REQUEST)
    request["routeTarget"] = {
        "providerId": "hithink", "upstreamSource": HITHINK_ETF_HISTORY_SOURCE,
        "routeIndex": 0,
    }
    frame = _target_frame()
    frame.attrs["upstream_source"] = HITHINK_ETF_HISTORY_SOURCE
    frame.attrs["thesis_ledger_v3_pagination"]["protocol"] = HITHINK_ETF_LOCAL_PAGINATION_PROTOCOL_V1
    execution = _execution(frame, provider="hithink", upstream_source=HITHINK_ETF_HISTORY_SOURCE)

    def response():
        if mode == "chart":
            return chart_response(request, execution)
        context = market_coverage_context_v3(
            symbol=request["symbol"], market=request["routeKey"]["market"],
            start=request["start"], end=request["end"],
        )
        return _market_data_v3_response(request, execution, context)

    frame.attrs["hithink_etf_field_units"] = hithink_etf_field_contract(
        symbol="", start="", end="", source_revision="",
    )
    unknown = response()
    frame.attrs["hithink_etf_field_units"] = hithink_etf_field_contract(
        symbol=request["symbol"], start=request["start"], end=request["end"],
        source_revision=HITHINK_ETF_SOURCE_CONTRACT_REVISION_V1,
    )
    known = response()
    assert known["sourcePriceBasis"]["fieldUnits"] == {"volume": "fund-unit", "amount": "CNY"}
    assert known["inputFingerprint"] != unknown["inputFingerprint"]
    assert known.get("bars", known.get("points")) == unknown.get("bars", unknown.get("points"))

    changed_request = {**request, "symbol": "510300.SH"}
    with pytest.raises(ValueError):
        native_field_units(frame.attrs, request["routeKey"], "hithink",
                           HITHINK_ETF_HISTORY_SOURCE, changed_request)


@pytest.mark.parametrize("field,value", [
    ("assetType", "STOCK"), ("adjustment", "hfq"), ("nativeAdjust", ""),
    ("providerId", "tushare"), ("upstreamSource", "sina"),
    ("endpoint", "stock_zh_a_hist"), ("volumeUnit", "hand"),
    ("amountUnit", "CNY"), ("valuesConverted", 0),
    ("contractVersion", "unverified"), ("independentSourceId", "akshare"),
])
def test_mismatched_contract_is_rejected(field, value):
    frame = annotate_exact_daily_contract(_target_frame(), "etf", "eastmoney", "qfq")
    frame.attrs["native_daily_contract"][field] = value
    with pytest.raises(ValueError):
        native_field_units(frame.attrs, REQUEST["routeKey"], "akshare", "eastmoney")


@pytest.mark.parametrize("mode", ["window", "chart"])
def test_units_survive_response_without_numeric_conversion_and_bind_fingerprint(monkeypatch, mode):
    import api.thesis_ledger as api
    import api.thesis_ledger_chart_v3 as chart

    for module in (api, chart):
        monkeypatch.setattr(module, "_now_iso", lambda: "2026-09-27T00:00:00+00:00")
    request = deepcopy(REQUEST)
    request["routeTarget"] = {"providerId": "akshare", "upstreamSource": "eastmoney", "routeIndex": 0}
    frame = _target_frame()
    execution = _execution(frame)

    def response():
        if mode == "chart":
            return chart_response(request, execution)
        context = market_coverage_context_v3(
            symbol=request["symbol"], market=request["routeKey"]["market"],
            start=request["start"], end=request["end"],
        )
        return _market_data_v3_response(request, execution, context)

    old = response()
    assert "fieldUnits" not in old["sourcePriceBasis"]
    annotate_exact_daily_contract(frame, "etf", "eastmoney", "qfq")
    current = response()
    assert current["sourcePriceBasis"]["fieldUnits"] == {"volume": "unknown", "amount": "unknown"}
    assert current["bars"] == old["bars"]
    assert current["inputFingerprint"] != old["inputFingerprint"]
    assert current["sourcePriceBasis"]["volumeBasis"] == "unknown"
    assert current["sourcePriceBasis"]["conversionAvailable"] is False
    frame.attrs["native_daily_contract"]["amountUnit"] = "CNY"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failure:
        response()
    assert failure.value.status_code == 502
