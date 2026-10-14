"""认证网关、实际 Control 路由与逐日结构化状态接缝。"""

from datetime import datetime, timezone
from types import SimpleNamespace
import json
from pathlib import Path
import subprocess

import pytest

from tests.test_thesis_ledger_public_fee_routes import configured_client, DATA_HEADERS, CONTROL_HEADERS
from src.services.thesis_ledger_trading_status import ROUTE_KEY
from src.services.thesis_ledger_baostock_status_source import TARGET, read_baostock_status_response, live_daily_status
from src.services.thesis_ledger_status_cache import HistoricalStatusCache

URL = "/api/v3/thesis-ledger/market/trading-status"


def request():
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "baostock-status",
            "routeKey": ROUTE_KEY, "routeTarget": {**TARGET, "routeIndex": 0},
            "symbol": "300757.SZ", "range": {"start": "2023-08-31", "end": "2023-09-01"}}


def value():
    return {"days": [{"date": "2023-08-31", "state": "suspended", "tradestatus": "0", "isST": "0"},
                     {"date": "2023-09-01", "state": "unknown", "tradestatus": "unknown", "isST": "unknown"}],
            "expectedSessions": ["2023-08-31", "2023-09-01"], "coverage": {"complete": False,
            "calendarUnknownDates": [], "statusUnknownDates": ["2023-09-01"], "stUnknownDates": ["2023-09-01"]},
            "responseSha256": "a" * 64, "observedAt": "2026-10-09T00:00:00+00:00"}


def policy():
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "status-policy", "revision": 1,
            "enabled": True, "routes": [{"key": ROUTE_KEY, "targets": [TARGET]}]}


def test_catalog_and_actual_authenticated_gateway_without_auto_saving_policy(configured_client, monkeypatch):
    client, runtime, store = configured_client
    calls = []
    monkeypatch.setattr("src.services.thesis_ledger_baostock_status_source.live_daily_status", lambda *args: calls.append(args) or value())
    runtime.adapters["baostock"] = SimpleNamespace()
    entries = client.get("/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3", headers=CONTROL_HEADERS).json()["entries"]
    assert next(e for e in entries if e["key"] == ROUTE_KEY and e["target"] == TARGET)["state"] == "ready"
    assert client.post(URL, json=request(), headers=DATA_HEADERS).status_code == 503
    assert not calls
    saved = client.post("/api/v3/thesis-ledger/control/policies/apply", json=policy(), headers=CONTROL_HEADERS)
    assert saved.status_code == 200 and saved.json()["effective"]["routes"][0]["targets"][0]["eligible"]
    assert store.get_route_admission_v3(key=ROUTE_KEY, target=TARGET) is None
    first = client.post(URL, json=request(), headers=DATA_HEADERS)
    assert first.status_code == 200, first.text
    response = first.json()
    assert response["days"][0]["state"] == "suspended"
    assert response["days"][0]["coarseST"] == "notFlagged"
    assert response["structuredCoverage"]["complete"] is False
    assert response["sourceDocuments"] == []
    assert response["sourceRecords"][0]["symbol"] == "300757.SZ"
    again = client.post(URL, json={**request(), "requestId": "second"}, headers=DATA_HEADERS)
    assert again.json()["requestId"] == "second" and len(calls) == 1
    assert client.post(URL, json=request()).status_code == 401
    assert client.post(URL, json={**request(), "routeTarget": {**TARGET, "routeIndex": 1}}, headers=DATA_HEADERS).status_code == 503
    # 保留可由消费仓 Schema 解析的真实 wrapper 响应；文件只在本次临时测试目录。
    Path(store.database_path).with_suffix('.response.json').write_text(json.dumps(response))


def test_changed_policy_during_read_rejects_response(configured_client, monkeypatch):
    client, runtime, store = configured_client
    store.apply_policy_v3(policy())
    runtime.adapters["baostock"] = SimpleNamespace()
    def change(adapter, wire):
        value_after_read = read_baostock_status_response(adapter, wire, reader=lambda *args:value())
        disabled = {**policy(), "revision": 2, "enabled": False}
        store.apply_policy_v3(disabled)
        return value_after_read
    monkeypatch.setattr("src.services.thesis_ledger_baostock_status_source.read_baostock_status_response", change)
    result = client.post(URL, json=request(), headers=DATA_HEADERS)
    assert result.status_code == 503 and result.json()["detail"]["code"] == "not_admitted"


def test_upstream_failure_never_returns_fake_zero_or_overwrites_cache(tmp_path):
    adapter = SimpleNamespace(_historical_status_cache=HistoricalStatusCache(tmp_path))
    def failure(*args):
        raise RuntimeError("source failed")
    with pytest.raises(RuntimeError):
        read_baostock_status_response(adapter, request(), reader=failure)
    assert not list(tmp_path.glob("*.json"))


def test_bounded_sdk_process_timeout_is_explicit(monkeypatch):
    from datetime import date
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 45
        raise subprocess.TimeoutExpired(args[0], 45)
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ValueError, match="超时"):
        live_daily_status("300757.SZ", date(2023, 8, 31), date(2023, 9, 1))
