"""交互图表端点的鉴权、固定目标与盘中观测边界。"""

from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger_chart_v3 import router
from src.services.thesis_ledger_provider_runtime import ProviderCallError

PATH = "/api/v3/thesis-ledger/market/chart-bars"
TARGET = {"providerId": "akshare", "upstreamSource": "eastmoney", "routeIndex": 0}
REQUEST = {
    "contractVersion": 3, "purpose": "interactive-chart", "requestId": "chart-test",
    "symbol": "159516.SZ", "start": "2026-05-18", "end": "2026-05-20",
    "routeKey": {"kind": "bar", "market": "CN", "assetType": "ETF",
                 "capability": "DAILY_BAR", "timeframe": "1d", "adjustment": "qfq"},
    "routeTarget": TARGET,
}


@pytest.fixture
def setup(monkeypatch):
    import api.thesis_ledger_chart_v3 as chart
    import src.services.thesis_ledger_provider_runtime as runtime

    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setattr(chart, "_now_iso", lambda: "2026-05-20T03:00:00+00:00")
    frame = pd.DataFrame([
        {"date": day, "open": 1, "close": 1.1, "high": 1.2, "low": 0.9,
         "volume": 100, "amount": 110}
        for day in ["2026-05-18", "2026-05-19", "2026-05-20"]
    ])
    frame.attrs["has_more_before"] = True
    execution = SimpleNamespace(value=frame, provider="akshare", upstream_source="eastmoney",
                                route_index=0, effective_revision=7)
    execute = Mock(return_value=execution)
    monkeypatch.setattr(runtime, "get_thesis_ledger_runtime",
                        lambda: SimpleNamespace(execute_market_bars_v3=execute))
    app = FastAPI()
    app.include_router(router, prefix="/api/v3")
    return TestClient(app), execute, execution


def post(client, payload=None):
    return client.post(PATH, headers={"Authorization": "Bearer test-token"},
                       json=REQUEST if payload is None else payload)


def test_incomplete_bar_and_observed_time_survive_refresh(setup):
    client, execute, _ = setup
    for _ in range(2):
        response = post(client)
        assert response.status_code == 200
        data = response.json()
        assert data["purpose"] == "interactive-chart"
        assert "coverageProof" not in data
        assert [point["completionStatus"] for point in data["bars"]] == [
            "complete", "complete", "incomplete"
        ]
        assert {point["availableAt"] for point in data["bars"]} == {"2026-05-20T03:00:00+00:00"}
        assert data["coverage"]["latestCompleteTradingDate"] == "2026-05-19"
        assert data["coverage"]["hasMoreBefore"] is True
    assert execute.call_count == 2
    args, kwargs = execute.call_args
    assert kwargs == {"route_target": TARGET}
    assert args[0].adjustment == "qfq"
    assert args[1] == REQUEST["routeKey"]


def test_unauthorized_never_dispatches(setup):
    client, execute, _ = setup
    assert client.post(PATH, json=REQUEST).status_code in {401, 503}
    assert client.post(PATH, json=REQUEST, headers={"Authorization": "Bearer wrong"}).status_code == 401
    execute.assert_not_called()


@pytest.mark.parametrize("field", ["purpose", "routeTarget", "requestId"])
def test_required_chart_identity(setup, field):
    client, execute, _ = setup
    payload = {key: value for key, value in REQUEST.items() if key != field}
    assert post(client, payload).status_code == 422
    execute.assert_not_called()


@pytest.mark.parametrize("change", ["source", "duplicate", "outside", "ohlc", "revision"])
def test_bad_provider_observations_fail_closed(setup, change):
    client, _, execution = setup
    if change == "source":
        execution.upstream_source = "other"
    elif change == "duplicate":
        execution.value.loc[1, "date"] = "2026-05-18"
    elif change == "outside":
        execution.value.loc[0, "date"] = "2026-05-17"
    elif change == "ohlc":
        execution.value.loc[0, "high"] = 0
    else:
        execution.effective_revision = True
    response = post(client)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "invalid_response"


@pytest.mark.parametrize("code,expected,status", [
    ("unsupported_adjustment", "unsupported_price_basis", 422),
    ("insufficient_coverage", "insufficient_coverage", 422),
    ("invalid_response", "invalid_response", 502),
    ("NO_ELIGIBLE_PROVIDER", "upstream_failure", 503),
])
def test_safe_errors(setup, code, expected, status):
    client, execute, _ = setup
    execute.side_effect = ProviderCallError(code, "private-upstream-response")
    response = post(client)
    assert response.status_code == status
    assert response.json()["error"]["code"] == expected
    assert "private-upstream-response" not in response.text


def test_route_is_mounted_in_app(tmp_path, monkeypatch):
    from api.app import create_app
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    client = TestClient(create_app(static_dir=tmp_path / "empty"))
    assert client.post(PATH, json=REQUEST).status_code == 401
