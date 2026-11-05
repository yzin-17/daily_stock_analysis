"""缓存汇率的抓取时点必须可解释，未知时区不能伪装成新抓取。"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient as ApiClient

from api.thesis_ledger import router_v3
from src.data.fx_rate_repository import FxRate


@pytest.mark.parametrize("inverse", [False, True])
@pytest.mark.parametrize(
    "updated_at",
    [
        datetime(2026, 10, 16, 8, 30),
        None,
        datetime(2026, 10, 16, 8, 30, tzinfo=timezone(timedelta(hours=8))),
    ],
)
def test_cached_fx_preserves_rate_and_only_emits_verifiable_timestamp(monkeypatch, inverse, updated_at):
    from src.services import fx_rate_service

    row = FxRate(
        id=1,
        from_currency="USD",
        to_currency="CNY",
        rate=7.25,
        rate_date=date(2026, 10, 16),
        source="yfinance",
        is_stale=False,
        updated_at=updated_at,
    )

    def read(*, from_currency, to_currency, as_of):
        assert as_of == row.rate_date
        if (from_currency, to_currency) == ("USD", "CNY"):
            return row
        return None

    repo = SimpleNamespace(get_latest_fx_rate=read, save_fx_rate=Mock(), close=Mock())
    fetch = Mock(side_effect=AssertionError("有效缓存不应触发网络"))
    monkeypatch.setattr(
        fx_rate_service, "FxRateRepository", lambda: repo,
    )
    monkeypatch.setattr(fx_rate_service, "_fetch_yahoo_rate", fetch)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "fx-test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    base, currency = ("USD", "CNY") if inverse else ("CNY", "USD")
    response = ApiClient(app).get(
        "/api/v3/thesis-ledger/market/fx-rates",
        params={"baseCurrency": base, "currencies": currency, "asOf": "2026-10-16"},
        headers={"authorization": "Bearer fx-test-token"},
    )
    assert response.status_code == 200
    result = next(item for item in response.json()["rates"] if item["fromCurrency"] == currency)
    assert result["rate"] == pytest.approx(1 / 7.25 if inverse else 7.25)
    assert result["provider"] == "yfinance"
    assert result["available"] is True
    assert result["rateDate"] == "2026-10-16"
    if updated_at is None or updated_at.tzinfo is None:
        assert "fetchedAt" not in result
    else:
        actual = datetime.fromisoformat(result["fetchedAt"])
        assert actual == updated_at
        assert actual.utcoffset() == timedelta(0)
    fetch.assert_not_called()
    repo.save_fx_rate.assert_not_called()
