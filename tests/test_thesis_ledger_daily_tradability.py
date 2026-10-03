"""日级可交易性生产与冻结时点负例。"""

from copy import deepcopy
from datetime import datetime, timezone

import pandas as pd
import pytest

from src.services.thesis_ledger_daily_tradability import daily_tradability_evidence
from src.services import thesis_ledger_dependency_facts as dependencies


def _input():
    dates = ["2026-07-29", "2026-07-30", "2026-07-31"]
    frame = pd.DataFrame([
        {"date": day, "open": 1, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 100, "amount": 110}
        for day in (dates[0], dates[2])
    ])
    source = {"provider": "fixture", "revision": "r1", "availableAt": "2026-07-01T00:00:00Z"}
    frame.attrs["daily_tradability_input"] = {
        "symbol": "159515.SZ", "range": {"start": dates[0], "end": dates[2]},
        "listing": {"listedOn": "2023-01-01", "source": deepcopy(source)},
        "calendar": {"source": deepcopy(source), "expectedSessions": dates},
        "responseSha256": "a" * 64, "observedAt": "2026-09-29T19:30:43.425847Z",
        "missingSessions": [dates[1]], "paginationComplete": True,
    }
    return {
        "bars": frame, "symbol": "159515.SZ", "instrument_type": "ETF", "start": dates[0], "end": dates[2],
        "data_as_of": datetime(2026, 9, 30, tzinfo=timezone.utc),
        "route_key": {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"},
        "route_target": {"providerId": "hithink", "upstreamSource": "fund-market-historical", "routeIndex": 0},
        "provider_revision": "admitted-r1",
    }


def test_missing_day_is_classified_and_response_preserves_evidence(monkeypatch):
    inputs = _input()
    evidence = daily_tradability_evidence(**inputs)
    assert [day["state"] for day in evidence["days"]] == [
        "observed-traded", "assumed-untradable-no-bar", "observed-traded",
    ]
    monkeypatch.setattr(dependencies, "real_cn_tradability", lambda *args, **kwargs: {
        "coverage": {"complete": True}, "tradable": True, "provider": "hithink",
        "providerRevision": "r1", "historicalTradability": evidence,
    })
    response = dependencies.instrument_facts_response(
        "159515.SZ", inputs["data_as_of"], inputs["start"], inputs["end"], inputs["start"], inputs["end"], [],
        instrument_type="ETF",
    )
    assert response["status"] == "supported"
    assert response["historicalTradability"] == evidence
    assert not any(item["category"] == "criticalFact" for item in response["missingInputs"])


@pytest.mark.parametrize("failure", ["future", "pagination", "hash", "missing", "zero", "duplicate", "listing", "calendar", "metadata", "revision", "calendar_source"])
def test_invalid_evidence_is_rejected(failure):
    inputs = _input()
    frame = inputs["bars"]
    metadata = frame.attrs["daily_tradability_input"]
    if failure == "future":
        metadata["observedAt"] = "2026-10-01T00:00:00Z"
    elif failure == "pagination":
        metadata["paginationComplete"] = False
    elif failure == "hash":
        metadata["responseSha256"] = "invalid"
    elif failure == "missing":
        metadata["missingSessions"] = []
    elif failure == "zero":
        frame.loc[0, "volume"] = 0
    elif failure == "duplicate":
        frame.loc[1, "date"] = frame.loc[0, "date"]
    elif failure == "listing":
        metadata["listing"]["listedOn"] = "2026-07-30"
    elif failure == "calendar":
        metadata["calendar"]["expectedSessions"].reverse()
    elif failure == "metadata":
        frame.attrs.clear()
    elif failure == "revision":
        inputs["provider_revision"] = "unknown"
    else:
        metadata["calendar"]["source"]["provider"] = ""
    with pytest.raises(ValueError):
        daily_tradability_evidence(**inputs)
