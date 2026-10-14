"""新增风险来源通过真实临时控制库与已认证状态网关。"""

from tests import test_thesis_ledger_public_fee_routes as routes
from tests.test_thesis_ledger_risk_price_limits import request
from src.services.thesis_ledger_trading_status import ROUTE_KEY, PROVIDER_ID, RISK_UPSTREAM_SOURCE

configured_client = routes.configured_client
TARGET = {"providerId": PROVIDER_ID, "upstreamSource": RISK_UPSTREAM_SOURCE}
URL = "/api/v3/thesis-ledger/market/trading-status"


def test_risk_catalog_policy_and_authenticated_production_read(configured_client):
    client, _runtime, store = configured_client
    catalog = client.get("/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3",
                         headers=routes.CONTROL_HEADERS).json()
    assert {"key": ROUTE_KEY, "target": TARGET, "state": "ready"} in catalog["entries"]
    policy = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "risk-policy",
              "revision": 1, "enabled": True, "routes": [{"key": ROUTE_KEY, "targets": [TARGET]}]}
    saved = client.post("/api/v3/thesis-ledger/control/policies/apply", json=policy,
                        headers=routes.CONTROL_HEADERS)
    assert saved.status_code == 200
    assert saved.json()["effective"]["routes"][0]["targets"][0]["eligible"]
    assert store.get_route_admission_v3(key=ROUTE_KEY, target=TARGET) is None
    response = client.post(URL, json=request(), headers=routes.DATA_HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["provenance"]["upstreamSource"] == RISK_UPSTREAM_SOURCE
    assert result["days"][0]["state"] == "unknown"
    assert result["days"][0]["dailyPriceLimit"]["maxUpRatio"] == "0.05"
    assert result["days"][2]["dailyPriceLimit"]["maxUpRatio"] == "0.1"
    assert result["days"][2]["riskWarning"]["delisting"] == "none"


def test_risk_source_still_requires_data_authentication(configured_client):
    client, _runtime, _store = configured_client
    for headers in ({}, routes.CONTROL_HEADERS):
        assert client.post(URL, json=request(), headers=headers).status_code == 401
