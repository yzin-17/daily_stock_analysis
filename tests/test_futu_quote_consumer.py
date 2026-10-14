"""产品报价 HTTP 将明确资产类型传至实际 Gateway，兼容旧请求。"""

from datetime import datetime, timezone
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from api.thesis_ledger import router_v3


@pytest.mark.parametrize("asset", ["ETF", "STOCK", "INDEX", None])
def test_http_quote_identity_and_old_request_compatibility(monkeypatch, asset):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-quote-consumer")
    monkeypatch.delenv("THESIS_LEDGER_FIXTURE_MODE", raising=False)
    calls = []
    quote = SimpleNamespace(price=10, open_price=10, high=11, low=9, pre_close=10,
        volume=0, amount=None, provider_timestamp=None, fetched_at=datetime.now(timezone.utc).isoformat(), source="futu")
    def read(symbol, **kwargs):
        calls.append((symbol, kwargs))
        return SimpleNamespace(data=quote, provider="futu", fallback_used=False)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway",
                        lambda: SimpleNamespace(quote=read))
    app = FastAPI(); app.include_router(router_v3)
    params = {"symbol": "SPY.US"}
    if asset is not None:
        params["assetType"] = asset
    with TestClient(app) as client:
        response = client.get("/thesis-ledger/market/quote", params=params,
                              headers={"authorization": "Bearer synthetic-quote-consumer"})
    assert response.status_code == 200, response.text
    assert response.json()["provider"] == "futu" and response.json()["volume"] == 0
    assert response.json()["upstreamSource"] == "futu"
    assert response.json()["amount"] is None and response.json()["marketTime"] is None
    assert calls[0][0] == "SPY.US"
    if asset is not None:
        assert calls[0][1]["instrument_type"] == asset
    else:
        assert "instrument_type" not in calls[0][1]


def test_invalid_asset_type_and_unauthorized_http_never_reach_gateway(monkeypatch):
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-quote-consumer")
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway",
                        lambda: pytest.fail("must not read gateway"))
    app = FastAPI(); app.include_router(router_v3)
    with TestClient(app) as client:
        assert client.get("/thesis-ledger/market/quote", params={"symbol": "SPY.US", "assetType": "ETF"}).status_code == 401
        assert client.get("/thesis-ledger/market/quote", params={"symbol": "SPY.US", "assetType": "OTHER"},
                          headers={"authorization": "Bearer synthetic-quote-consumer"}).status_code == 422
