"""未知上市生产合同与既有研究来源边界。"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.thesis_ledger_listing_information import (
    canonical_listing_information, daily_listing_date, daily_listing_information,
)
from src.services.thesis_ledger_market_v3_facts import (
    canonical_market_coverage_proof_v3, market_coverage_context_v3, market_listing_fact_v3,
)
from src.services.thesis_ledger_research_calendar import research_market_coverage_context
from src.services.thesis_ledger_market_daily_tradability import market_daily_tradability
from src.services.thesis_ledger_market_tradability_input import market_tradability_frame
from src.services.thesis_ledger_provider_runtime import ProviderCallError

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)


def request():
    return {
        "symbol": "600519.SH", "start": "2023-08-23", "end": "2023-08-29",
        "tradabilityMode": "assume-untradable-no-bar",
        "routeKey": {"kind": "bar", "market": "CN", "assetType": "STOCK", "capability": "DAILY_BAR",
                     "timeframe": "1d", "adjustment": "qfq"},
        "routeTarget": {"providerId": "tencent", "upstreamSource": "tencent", "routeIndex": 0},
    }


def sample():
    payload = request()
    context = research_market_coverage_context(payload, observed_at=NOW)
    days = context["expectedPostListingSessionDates"]
    frame = pd.DataFrame([
        {"date": day, "open": 1, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 100, "amount": 110}
        for day in days
    ])
    execution = SimpleNamespace(value=frame, provider="tencent", upstream_source="tencent", provider_revision="r1")
    return payload, context, days, execution


def test_unknown_keeps_all_sessions_and_default_context_stays_unavailable():
    payload = request()
    context = research_market_coverage_context(payload, observed_at=NOW)
    assert context["listing"] == {"symbol": payload["symbol"], "status": "unknown"}
    assert context["expectedPostListingSessionDates"] == context["calendar"]["expectedSessionDates"]
    assert market_coverage_context_v3(symbol=payload["symbol"], market="CN", start=payload["start"],
                                      end=payload["end"]) is None
    assert canonical_listing_information(context["listing"]) == ["600519.SH", "unknown"]


@pytest.mark.parametrize("change", ["mode", "target", "provider", "source", "asset", "market", "timeframe", "adjustment"])
def test_other_requests_still_require_existing_listing(change):
    payload = request()
    if change == "mode":
        payload.pop("tradabilityMode")
    elif change == "target":
        payload.pop("routeTarget")
    elif change in {"provider", "source"}:
        payload["routeTarget"]["providerId" if change == "provider" else "upstreamSource"] = "hithink"
    else:
        field, value = {"asset": ("assetType", "ETF"), "market": ("market", "HK"),
                        "timeframe": ("timeframe", "1m"), "adjustment": ("adjustment", "hfq")}[change]
        payload["routeKey"][field] = value
    assert research_market_coverage_context(payload, observed_at=NOW) is None


@pytest.mark.parametrize("symbol", ["600000.SH", "000995.SZ", "159516.SZ"])
def test_existing_listing_keeps_exact_fact(symbol):
    payload = {**request(), "symbol": symbol}
    context = research_market_coverage_context(payload, observed_at=NOW)
    assert context["listing"] == market_listing_fact_v3(symbol)
    assert "status" not in context["listing"]


@pytest.mark.parametrize("extra", [
    {"firstTradingDate": "2023-08-23"}, {"firstTradingDate": None},
    {"source": "invented"}, {"revision": "r1"}, {"knownAt": "2023-08-23T00:00:00Z"},
])
def test_unknown_market_rejects_dates_and_sources(extra):
    value = {"symbol": "600519.SH", "status": "unknown", **extra}
    with pytest.raises(ValueError):
        canonical_listing_information(value)
    with pytest.raises(ValueError):
        daily_listing_information(value)


@pytest.mark.parametrize("value", [
    {"status": "pending"}, {"status": "unknown", "listedOn": "2023-08-23"},
    {"status": "unknown", "source": {}}, {"status": "unknown", "listedOn": None},
    {"status": "unknown", "symbol": "600519.SH"}, {"listedOn": "2023-08-23", "source": {}},
])
def test_unknown_daily_rejects_extra_or_incomplete_fields(value):
    with pytest.raises(ValueError):
        daily_listing_date(value)


def test_known_coverage_encoding_keeps_shared_golden():
    path = Path(__file__).resolve().parents[2] / "thesis-ledger/packages/schemas/fixtures/market-data-v3.response.etf-qfq.json"
    if not path.is_file():
        pytest.skip("单仓环境没有共享 fixture；同仓已知回归仍执行")
    proof = json.loads(path.read_text())["coverageProof"]
    encoded = json.dumps(canonical_market_coverage_proof_v3(proof), ensure_ascii=False, separators=(",", ":"))
    assert sha256(encoded.encode()).hexdigest() == "9b4e93931d93cece2c8492afaa79657916829291a5abf8153ce833e1d7e88703"


def test_daily_unknown_preserves_explicit_missing_session_and_raw_frame():
    payload, context, days, execution = sample()
    execution.value = execution.value.iloc[1:].copy()
    original = deepcopy(execution.value.attrs)
    result = market_daily_tradability(payload, execution, context, days[1:], NOW.isoformat())[0]
    assert result["listing"] == {"status": "unknown"}
    assert result["days"][0] == {"date": days[0], "state": "assumed-untradable-no-bar"}
    assert execution.value.attrs == original
    assert result == market_daily_tradability(payload, execution, context, days[1:], NOW.isoformat())[0]


@pytest.mark.parametrize("invalid", ["date", "price", "volume", "identity", "future-calendar", "listing-mismatch"])
def test_unknown_does_not_hide_invalid_input(invalid):
    payload, context, days, execution = sample()
    if invalid == "date":
        days[0] = "2023-08-22"
    elif invalid == "price":
        execution.value.loc[0, "close"] = -1
    elif invalid == "volume":
        execution.value.loc[0, "volume"] = 0
    else:
        execution.value = market_tradability_frame(payload, execution.value, context, days, NOW.isoformat())
        metadata = execution.value.attrs["daily_tradability_input"]
        if invalid == "identity":
            metadata["symbol"] = "600000.SH"
        elif invalid == "future-calendar":
            metadata["calendar"]["source"]["availableAt"] = "2027-01-01T00:00:00Z"
        else:
            metadata["listing"] = daily_listing_information(market_listing_fact_v3("600000.SH"))
    with pytest.raises(ProviderCallError):
        market_daily_tradability(payload, execution, context, days, NOW.isoformat())
