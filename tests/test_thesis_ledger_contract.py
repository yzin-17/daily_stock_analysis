"""ThesisLedger Contract V1 的确定性测试。"""

from datetime import date
import re

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import _fx_row, router
from api.middlewares.error_handler import add_error_handlers


def _client(monkeypatch, *, fixture: bool = True) -> TestClient:
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true" if fixture else "false")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_contract_requires_independent_bearer_token(monkeypatch):
    client = _client(monkeypatch)
    response = client.get("/api/v1/thesis-ledger/capabilities")
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "unauthorized"


def test_contract_fixture_exposes_capabilities_and_daily_quote(monkeypatch):
    client = _client(monkeypatch)
    headers = {"authorization": "Bearer test-token"}

    capabilities_response = client.get(
        "/api/v1/thesis-ledger/capabilities",
        headers=headers,
    )
    quote_response = client.get(
        "/api/v1/thesis-ledger/market/quote?symbol=600519.SH",
        headers=headers,
    )

    assert capabilities_response.status_code == 200
    assert capabilities_response.json()["contractVersion"] == 1
    assert capabilities_response.json()["capabilities"]["bars"]["timeframes"] == ["1d"]
    assert capabilities_response.json()["capabilities"]["chip"]["capability"] == "CHIP_SUMMARY"
    assert quote_response.status_code == 200
    assert quote_response.json()["symbol"] == "600519.SH"
    assert quote_response.json()["version"] == 1


def test_v2_capability_contract_distinguishes_markets_timeframes_and_facts(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/v2/capabilities",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 2
    capabilities = {
        (item["market"], item["instrumentType"], item["timeframe"]): item
        for item in payload["capabilities"]
    }
    assert capabilities[("CN", "STOCK", "1m")]["kind"] == "base"
    assert capabilities[("CN", "STOCK", "1m")]["range"] == {
        "start": "2025-01-10",
        "end": "2025-01-10",
    }
    assert capabilities[("CN", "STOCK", "1d")]["range"] == {
        "start": "2024-11-12",
        "end": "2025-01-10",
    }
    assert capabilities[("HK", "STOCK", "15m")]["kind"] == "derived"
    assert capabilities[("HK", "NAV_FUND", "1d")]["status"] == "unsupported"
    assert capabilities[("US", "ETF", "1d")]["status"] == "supported"
    assert {calendar["market"] for calendar in payload["calendars"]} == {"CN", "HK", "US"}
    assert all(
        calendar["range"] == {"start": "2025-01-10", "end": "2025-01-10"}
        for calendar in payload["calendars"]
    )
    assert {fact["currency"] for fact in payload["instrumentFacts"]} == {"CNY", "HKD", "USD"}
    assert all(isinstance(fact["tickSize"], str) for fact in payload["instrumentFacts"])
    assert all(isinstance(fact["lotSize"], str) for fact in payload["instrumentFacts"])
    assert payload["fx"]["status"] == "supported"
    assert all(isinstance(fact["rate"], str) for fact in payload["fx"]["facts"])
    assert payload["corporateActions"]["status"] == "supported"
    assert payload["nav"]["status"] == "supported"
    assert isinstance(payload["nav"]["facts"][0]["nav"], str)


def test_v2_non_fixture_does_not_expose_fixture_facts_or_static_support(monkeypatch):
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v1/thesis-ledger/v2/capabilities",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["instrumentFacts"] == []
    assert payload["fx"]["facts"] == []
    assert payload["corporateActions"]["facts"] == []
    assert payload["nav"]["facts"] == []
    assert payload["fx"]["status"] == "unavailable"
    assert payload["corporateActions"]["status"] == "unavailable"
    assert payload["nav"]["status"] == "unavailable"
    assert payload["calendars"] == []
    assert all(
        item["providerRevision"] == "dsa-backtest-v2-contract-unavailable"
        and item["range"] == {"start": None, "end": None}
        for item in payload["capabilities"]
        if item["kind"] == "base"
    )
    assert all(
        item["status"] != "supported"
        for item in payload["capabilities"]
        if item["kind"] == "base"
    )


def test_v2_non_fixture_declares_only_registry_confirmed_cn_daily_bars(monkeypatch):
    import api.thesis_ledger as thesis_ledger_api

    monkeypatch.setattr(
        thesis_ledger_api,
        "_real_daily_bar_provider_route",
        lambda: ("efinance", "akshare"),
    )
    client = _client(monkeypatch, fixture=False)
    response = client.get(
        "/api/v1/thesis-ledger/v2/capabilities",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    by_key = {
        (item["market"], item["instrumentType"], item["timeframe"]): item
        for item in payload["capabilities"]
        if item["kind"] == "base"
    }
    assert by_key[("CN", "STOCK", "1d")]["status"] == "supported"
    assert by_key[("CN", "STOCK", "1d")]["provider"] == "akshare"
    for key, item in by_key.items():
        if key != ("CN", "STOCK", "1d"):
            expected = "unsupported" if key[1] == "NAV_FUND" and key[0] != "CN" else "unavailable"
            assert item["status"] == expected


def test_v2_fixture_exposes_deterministic_minute_bars(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/v2/market/bars?symbol=600519.SH&timeframe=1m&limit=3",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    bars = response.json()
    assert len(bars) == 3
    assert all(bar["timeframe"] == "1m" for bar in bars)
    assert all(bar["availableAt"] >= bar["occurredAt"] for bar in bars)
    assert all(isinstance(bar["close"], str) for bar in bars)
    assert all(isinstance(bar["volume"], str) for bar in bars)


def test_v2_fixture_exposes_daily_facts_without_rebuilding_them(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/v2/market/bars?symbol=600519.SH&timeframe=1d&limit=1",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    bar = response.json()[0]
    assert bar["timeframe"] == "1d"
    assert bar["occurredAt"] == bar["timestamp"]
    assert bar["openedAt"].endswith("01:30:00+00:00")
    assert bar["openAvailableAt"] == bar["openedAt"]
    assert bar["market"] == "CN"
    assert isinstance(bar["open"], str)
    assert isinstance(bar["amount"], str)


def test_v2_non_fixture_daily_bars_serialize_decimal_strings(monkeypatch):
    client = _client(monkeypatch, fixture=False)
    monkeypatch.setattr(
        "api.thesis_ledger._real_bars",
        lambda *args, **kwargs: [
            {
                "version": 1,
                "symbol": "600519.SH",
                "timeframe": "1d",
                "timestamp": "2025-01-10T07:00:00+00:00",
                "open": 0.3,
                "high": 1e-7,
                "low": -0.0,
                "close": 10.5,
                "volume": 3,
                "amount": 0.30000000000000004,
                "provider": "mock",
                "fetchedAt": "2025-01-10T08:00:00+00:00",
            }
        ],
    )

    response = client.get(
        "/api/v1/thesis-ledger/v2/market/bars?symbol=600519.SH&timeframe=1d",
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    bar = response.json()[0]
    for field in ("open", "high", "low", "close", "volume", "amount"):
        assert re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?", bar[field])
    assert bar["open"] == "0.3"
    assert bar["high"] == "0.0000001"
    assert bar["low"] == "0"
    assert bar["amount"] == "0.30000000000000004"
    assert bar["openedAt"] == "2025-01-10T01:30:00+00:00"
    assert bar["openAvailableAt"] == bar["openedAt"]


def test_v2_non_fixture_daily_bars_use_collection_time_when_provider_omits_fetch_time(monkeypatch):
    client = _client(monkeypatch, fixture=False)
    monkeypatch.setattr(
        "api.thesis_ledger._real_bars",
        lambda *args, **kwargs: [
            {
                "version": 1,
                "symbol": "600519.SH",
                "timeframe": "1d",
                "timestamp": "2025-01-10T06:00:00+00:00",
                "open": 1,
                "high": 2,
                "low": 1,
                "close": 1.5,
                "volume": 10,
                "amount": 15,
                "provider": "mock",
            }
        ],
    )

    response = client.get(
        "/api/v1/thesis-ledger/v2/market/bars?symbol=600519.SH&timeframe=1d",
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    bar = response.json()[0]
    assert bar["availableAt"] >= bar["occurredAt"]
    assert bar["availableAt"] != bar["occurredAt"]


def test_v2_session_close_is_pit_safe_for_unclosed_and_future_bars():
    from api.thesis_ledger import _v2_session_close_available_at

    assert (
        _v2_session_close_available_at(
            "2025-01-10T06:00:00+00:00",
            "2025-01-10T06:30:00+00:00",
        )
        is None
    )
    assert (
        _v2_session_close_available_at(
            "2025-01-10T07:00:00+00:00",
            "2025-01-10T06:30:00+00:00",
        )
        is None
    )
    assert _v2_session_close_available_at(
        "2025-01-10T06:00:00+00:00",
        "2025-01-11T00:00:00+00:00",
    ) == "2025-01-10T07:00:00+00:00"


def test_contract_returns_structured_unsupported_error(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/market/bars?symbol=600519.SH&timeframe=1m",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_capability"


def test_global_error_handler_preserves_contract_error_detail(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    add_error_handlers(app)
    client = TestClient(app)

    response = client.get(
        "/api/v1/thesis-ledger/market/bars?symbol=600519.SH&timeframe=1m",
        headers={"authorization": "Bearer test-token"},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["contractVersion"] == 1
    assert response.json()["detail"]["code"] == "unsupported_capability"


def test_contract_fixture_exposes_fund_nav(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/market/fund-nav?symbol=000001.OF",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == 1
    assert payload["symbol"] == "000001.OF"
    assert payload["unitNav"] == 1.2345
    assert payload["freshness"] == "delayed"


def test_contract_fixture_exposes_fx_rates_with_age_contract(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/market/fx-rates?baseCurrency=CNY&currencies=HKD,USD&asOf=2025-01-10",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["baseCurrency"] == "CNY"
    assert payload["maxAgeDays"] == 7
    assert {row["fromCurrency"] for row in payload["rates"]} == {"CNY", "HKD", "USD"}
    hkd = next(row for row in payload["rates"] if row["fromCurrency"] == "HKD")
    assert hkd["rate"] == 0.92
    assert hkd["available"] is True
    assert hkd["freshness"] == "delayed"


def test_contract_fixture_supports_inverse_and_cross_currency_rates(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/market/fx-rates?baseCurrency=HKD&currencies=CNY,USD&asOf=2025-01-10",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    rows = {row["fromCurrency"]: row for row in response.json()["rates"]}
    assert rows["CNY"]["rate"] == 1 / 0.92
    assert rows["USD"]["rate"] == 7.2 / 0.92


def test_contract_rejects_unknown_fx_currency(monkeypatch):
    client = _client(monkeypatch)
    response = client.get(
        "/api/v1/thesis-ledger/market/fx-rates?baseCurrency=CNY&currencies=JPY",
        headers={"authorization": "Bearer test-token"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_currency"


def test_fx_age_marks_stale_within_seven_days_and_blocks_after_threshold():
    recent = _fx_row(
        from_currency="HKD",
        to_currency="CNY",
        rate=0.92,
        rate_date=date(2025, 1, 3),
        provider="cache",
        fetched_at="2025-01-03T00:00:00+00:00",
        stale=False,
        as_of=date(2025, 1, 10),
    )
    expired = _fx_row(
        from_currency="HKD",
        to_currency="CNY",
        rate=0.92,
        rate_date=date(2025, 1, 2),
        provider="cache",
        fetched_at="2025-01-02T00:00:00+00:00",
        stale=False,
        as_of=date(2025, 1, 10),
    )
    assert recent["stale"] is True
    assert recent["available"] is True
    assert recent["ageDays"] == 7
    assert expired["available"] is False
