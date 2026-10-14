"""上市/退市生产包通过实际控制路由和鉴权网关。"""

import pytest

from tests import test_thesis_ledger_public_fee_routes as routes
from src.services.thesis_ledger_trading_status import PROVIDER_ID, ROUTE_KEY

configured_client = routes.configured_client


@pytest.mark.parametrize("source,symbol,start,end,phase", [
    ("cn-stock-initial-listing", "603350.SH", "2024-07-02", "2024-07-11", "notListed"),
    ("cn-stock-delisting-period", "600462.SH", "2025-07-10", "2025-07-22", "delistingPeriod"),
])
def test_real_source_catalog_policy_and_authenticated_read(configured_client, source, symbol, start, end, phase):
    client, _runtime, _store = configured_client
    target = {"providerId": PROVIDER_ID, "upstreamSource": source}
    catalog = client.get("/api/v3/thesis-ledger/control/routes/capabilities?contractVersion=3",
                         headers=routes.CONTROL_HEADERS).json()
    assert {"key": ROUTE_KEY, "target": target, "state": "ready"} in catalog["entries"]
    policy = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "listing-policy", "revision": 1,
              "enabled": True, "routes": [{"key": ROUTE_KEY, "targets": [target]}]}
    assert client.post("/api/v3/thesis-ledger/control/policies/apply", json=policy, headers=routes.CONTROL_HEADERS).status_code == 200
    request = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "listing-read",
               "routeKey": ROUTE_KEY, "routeTarget": {**target, "routeIndex": 0}, "symbol": symbol,
               "range": {"start": start, "end": end}}
    assert client.post("/api/v3/thesis-ledger/market/trading-status", json=request, headers={}).status_code == 401
    response = client.post("/api/v3/thesis-ledger/market/trading-status", json=request, headers=routes.DATA_HEADERS)
    assert response.status_code == 200
    result = response.json()
    assert result["days"][0]["listingPhase"] == phase
    assert result["provenance"]["upstreamSource"] == source
    assert all(doc["sha256"] and doc["observedAt"] for doc in result["sourceDocuments"])
