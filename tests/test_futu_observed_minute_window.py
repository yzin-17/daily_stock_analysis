"""已观测分钟回测窗口的正反例；HTTP 使用隔离控制库和构造的 SDK 响应。"""

from datetime import datetime, timedelta

import pandas as pd
import pytest

from api.thesis_ledger import router_v3
from data_provider.futu_market import FutuMarketAdapter
from tests.test_configured_market_routes import store  # noqa: F401
from tests.test_configured_market_routes import apply
from tests.test_futu_minute import row, sdk
from tests.test_thesis_ledger_futu_capability_path import configure, worker_transport
from tests.test_thesis_ledger_futu_minute_capability_path import client_for, request


@pytest.mark.parametrize("case", ["complete", "missing", "duplicate", "untraded", "wrong-window",
                                  "pending", "no-pages", "other-source", "other-asset"])
def test_minute_window(monkeypatch, store, case):
    configure(store)
    payload = request("CN", "STOCK", "600000.SH")
    del payload["purpose"]
    payload["tradabilityMode"] = "observed-bars-only"
    start = datetime.fromisoformat("2026-05-18T00:00")
    stamps = [570, *range(571, 691), *range(781, 901)]
    if case == "missing":
        stamps.remove(630)
    if case == "duplicate":
        stamps.insert(61, 630)
    rows = [row((start + timedelta(minutes=i)).isoformat(sep=" "), code="SH.600000",
                volume=0 if case == "untraded" else 50) for i in stamps]
    upstream, ctx = sdk([(0, pd.DataFrame(rows[:100]), b"next"), (0, pd.DataFrame(rows[100:]), None)])
    ctx.get_stock_basicinfo.return_value = 0, pd.DataFrame([{"code": "SH.600000", "stock_type": "STOCK"}])
    worker_transport(monkeypatch, upstream)
    target = {"providerId": "futu", "upstreamSource": "futu"}
    apply(store, payload["routeKey"], target)
    if case in {"wrong-window", "pending", "no-pages"}:
        read = FutuMarketAdapter.get_exact_minute
        def changed(self, query):
            result = read(self, query)
            pages = result["attrs"]["pagination"]
            if case == "wrong-window":
                pages["requestedEnd"] = "2026-05-19"
            elif case == "pending":
                pages["continuationPending"] = True
            else:
                del result["attrs"]["pagination"]
            return result
        monkeypatch.setattr(FutuMarketAdapter, "get_exact_minute", changed)
    if case == "other-source":
        payload["routeTarget"]["upstreamSource"] = "other"
    if case == "other-asset":
        payload["symbol"] = "510300.SH"
        payload["routeKey"]["assetType"] = "ETF"
    client, _ = client_for(monkeypatch, store)
    client.app.include_router(router_v3, prefix="/api/v1")
    res = client.post("/api/v1/thesis-ledger/market/bars", json=payload,
                      headers={"authorization": "Bearer synthetic-minute-token"})
    body = res.json()
    if case == "complete":
        assert res.status_code == 200, body
        assert len(body["bars"]) == 240 and body["bars"][59]["timestamp"].endswith("10:30:00+08:00")
        assert body["timestampConvention"] == "interval-end"
        assert body["coverageProof"]["pagination"] == {"status": "complete", "pagesFetched": 2, "continuationPending": False}
        assert body["sourcePriceBasis"]["fieldUnits"]["volume"] == "share"
        assert body["sourcePriceBasis"]["volumeBasis"] == "original"
        assert body["historicalTradabilityWindows"][0]["days"] == [{"date": payload["start"], "state": "observed-traded"}]
    else:
        assert res.status_code >= 400 and "bars" not in body
        if case in {"other-source", "other-asset"}:
            ctx.request_history_kline.assert_not_called()
