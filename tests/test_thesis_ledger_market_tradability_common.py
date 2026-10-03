"""公共日线状态由已取得价格自动构造，保留缺日和坏数据检查。"""

from copy import deepcopy
from hashlib import sha256
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from api.thesis_ledger import _market_data_v3_response
from data_provider.tencent_native_daily import TENCENT_NATIVE_DAILY_PROTOCOL
from src.services.thesis_ledger_market_v3_facts import market_coverage_context_v3
from src.services.thesis_ledger_market_daily_tradability import market_daily_tradability
from src.services.thesis_ledger_provider_runtime import ProviderCallError


def sample():
    days = ["2026-07-09", "2026-07-10"]
    request = {
        "contractVersion": 3, "requestId": "common-daily-state", "symbol": "159516.SZ",
        "routeKey": {"kind": "bar", "market": "CN", "assetType": "ETF", "capability": "DAILY_BAR",
                     "timeframe": "1d", "adjustment": "hfq"},
        "start": days[0], "end": days[-1], "tradabilityMode": "assume-untradable-no-bar",
    }
    frame = pd.DataFrame([
        {"date": pd.Timestamp(day), "open": 1, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 100, "amount": 110}
        for day in days
    ])
    parts = [{"start": days[0], "end": days[-1], "rows": 2, "sha256": "a" * 64}]
    frame.attrs.update({
        "upstream_source": "tencent", "has_more_before": False,
        "tencentDailyRetrieval": {
            "endpoint": "newfqkline/get", "adjustment": "hfq", "partitionComplete": True,
            "partitions": parts, "revision": sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        },
        "thesis_ledger_v3_pagination": {
            "status": "complete", "pagesFetched": 1, "continuationPending": False,
            "protocol": TENCENT_NATIVE_DAILY_PROTOCOL, "maximumRows": 800,
            "requestedStart": days[0], "requestedEnd": days[-1],
        },
    })
    execution = SimpleNamespace(value=frame, provider="tencent", upstream_source="tencent",
                                provider_revision="tencent-native-v1", route_index=0, effective_revision=3)
    context = market_coverage_context_v3(symbol="159516.SZ", market="CN", start=days[0], end=days[-1])
    assert context is not None
    return request, execution, context, days


def test_native_tencent_response_supplies_daily_state_without_private_metadata():
    request, execution, context, days = sample()
    original = deepcopy(execution.value.attrs)
    response = _market_data_v3_response(request, execution, context)
    assert len(response["bars"]) == 2
    evidence = response["historicalTradabilityWindows"][0]
    assert evidence["days"] == [{"date": day, "state": "observed-traded"} for day in days]
    assert evidence["barSource"]["routeTarget"] == {"providerId": "tencent", "upstreamSource": "tencent"}
    assert len(evidence["barSource"]["responseSha256"]) == 64
    assert execution.value.attrs == original


def test_same_captured_prices_have_stable_digest_and_changed_price_changes_it():
    request, execution, context, days = sample()
    def read():
        return market_daily_tradability(request, execution, context, days, "2026-10-03T00:00:00Z")[0]
    first = read()["barSource"]["responseSha256"]
    assert read()["barSource"]["responseSha256"] == first
    execution.value.loc[0, "close"] = 1.15
    assert read()["barSource"]["responseSha256"] != first


def test_missing_session_is_only_an_explicit_no_trade_assumption():
    request, execution, context, days = sample()
    execution.value = execution.value.iloc[:1].copy()
    evidence = market_daily_tradability(request, execution, context, days[:1], "2026-10-03T00:00:00Z")[0]
    assert evidence["days"][1] == {"date": days[1], "state": "assumed-untradable-no-bar"}
    del request["tradabilityMode"]
    with pytest.raises(ProviderCallError, match="未完整覆盖"):
        market_daily_tradability(request, execution, context, days[:1], "2026-10-03T00:00:00Z")


@pytest.mark.parametrize("invalid", ["duplicate", "outside", "zero_price", "zero_volume", "nan", "high_low"])
def test_invalid_rows_are_still_rejected(invalid):
    request, execution, context, days = sample()
    if invalid == "duplicate":
        days[1] = days[0]
    elif invalid == "outside":
        days[0] = "2026-07-08"
    elif invalid == "zero_price":
        execution.value.loc[0, "open"] = 0
    elif invalid == "zero_volume":
        execution.value.loc[0, "volume"] = 0
    elif invalid == "nan":
        execution.value.loc[0, "close"] = float("nan")
    else:
        execution.value.loc[0, "high"] = 0.5
    with pytest.raises(ProviderCallError):
        market_daily_tradability(request, execution, context, days, "2026-10-03T00:00:00Z")


def test_incomplete_pagination_is_rejected_before_day_state_generation():
    request, execution, context, _ = sample()
    execution.value.attrs["thesis_ledger_v3_pagination"]["continuationPending"] = True
    with pytest.raises(HTTPException) as error:
        _market_data_v3_response(request, execution, context)
    assert error.value.status_code in {422, 502}
