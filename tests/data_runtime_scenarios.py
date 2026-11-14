"""独立 HTTP 验收的受控原生输入；最终 DTO 始终由真实数据模块生成。"""

from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
import os
from types import SimpleNamespace


def post_case(method, path, body, *, status=200):
    return {"client": method, "method": "POST", "path": "/api/v3/thesis-ledger" + path,
            "body": body, "status": status}


def configure(name):
    import pytest
    import src.services.thesis_ledger_provider_runtime as runtime_module

    patches = pytest.MonkeyPatch()
    cases = []
    if name == "basics":
        from api.thesis_ledger import _bar_series_fingerprint
        from tests.test_thesis_ledger_indicator_calculation import IDENTITY, _points
        from src.data.fx_rate_repository import FxRateRepository

        repository = FxRateRepository()
        repository.save_fx_rate(from_currency="USD", to_currency="CNY", rate_date=date(2026, 10, 16),
                                rate=7.1, source="controlled-original-cache")
        repository.close()
        calendar = {"market": "CN", "start": "2026-09-01", "end": "2026-09-04",
                    "dataAsOf": "2026-10-16T12:00:00Z"}
        for method, path in [("marketCalendar", "/market/calendar"), ("backtestCalendar", "/backtest/calendar")]:
            cases.append({"client": method, "method": "GET", "path": "/api/v3/thesis-ledger" + path,
                          "query": calendar, "status": 200})
        cases.append({"client": "fxRates", "method": "GET", "path": "/api/v3/thesis-ledger/market/fx-rates",
                      "query": {"baseCurrency": "CNY", "currencies": "USD,CNY", "asOf": "2026-10-16"},
                      "status": 200})
        for count in (30, 2):
            points = _points(count)
            body = {"contractVersion": 3, "identity": IDENTITY, "points": points,
                    "inputFingerprint": _bar_series_fingerprint(points, IDENTITY),
                    "requests": [{"name": "MA", "parameters": {"period": 5}},
                                 {"name": "RSI", "parameters": {"short": 3, "mid": 5, "long": 8}}]}
            cases.append(post_case("calculateIndicators", "/market/indicators/calculate", body))
        cases.append(post_case("calculateIndicators", "/market/indicators/calculate",
                               {**cases[-1]["body"], "inputFingerprint": "invalid"}, status=422))
    elif name == "bars":
        from tests.test_thesis_ledger_market_v3 import REQUEST, ROUTE_KEY, _RuntimeStore, _frame, _target_session_dates
        from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime

        policy = {"contractVersion": 3, "revision": 12, "enabled": True, "routes": [{"key": ROUTE_KEY,
                  "targets": [{"providerId": "tencent", "upstreamSource": "tencent", "routeIndex": 0,
                               "eligible": True, "reason": None}]}]}

        class NativeFrames:
            def get_daily_data_for_source(self, symbol, source, **options):
                frame = _frame(_target_session_dates())
                frame.attrs["upstream_source"] = source
                frame.attrs["tencentDailyRetrieval"] = {
                    "endpoint": "newfqkline/get", "adjustment": options["adjustment"], "partitionComplete": True,
                    "partitions": [{"start": options["start_date"], "end": options["end_date"], "rows": len(frame),
                                    "sha256": sha256(frame.to_json().encode()).hexdigest()}],
                }
                partitions = frame.attrs["tencentDailyRetrieval"]["partitions"]
                frame.attrs["tencentDailyRetrieval"]["revision"] = sha256(
                    json.dumps(partitions, sort_keys=True, separators=(",", ":")).encode(),
                ).hexdigest()
                return frame

        runtime = ThesisLedgerProviderRuntime(_RuntimeStore(policy), adapters={"tencent": NativeFrames()})
        patches.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: runtime)
        body = deepcopy(REQUEST)
        cases.append(post_case("marketBarsV3", "/market/bars", body))
        chart = {**body, "purpose": "interactive-chart",
                 "routeTarget": {"providerId": "tencent", "upstreamSource": "tencent", "routeIndex": 0}}
        cases.append(post_case("marketChartBarsV3", "/market/chart-bars", chart))
        cases.append(post_case("marketBarsV3", "/market/bars", {**body, "symbol": "600519.SH"}, status=422))
    elif name == "events":
        from tests.test_thesis_ledger_events_v3 import state
        import data_provider.eastmoney_cash_capability as source

        runtime, body, read, *_ = state.__wrapped__()
        patches.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: runtime)
        patches.setattr(source, "fetch_fund_dividend_observations", read)
        cases.append(post_case("marketEventsV3", "/market/events", body))
        cases.append(post_case("marketEventsV3", "/market/events", {**body, "symbol": "159516.SZ"}, status=422))
    elif name == "nav":
        from tests.test_thesis_ledger_nav_production import state
        from src.services import thesis_ledger_nav_production as production
        from src.services import thesis_ledger_nav_calendar as calendar
        from src.services.thesis_ledger_nav_calendar_source import read_nav_research_workdays

        runtime, body, readers, *_ = state.__wrapped__(patches)
        # 跨仓验收使用已核验日历包，保留单元测试的其他原始输入与受控时钟。
        patches.setattr(calendar, "read_nav_research_workdays", read_nav_research_workdays)
        patches.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: runtime)
        patches.setattr(production, "read_fund_identity", readers["identity_reader"])
        patches.setattr(production, "read_fund_nav_evidence", readers["nav_reader"])
        cases.append(post_case("marketNavInputsV3", "/backtest/nav-inputs", body))
        cases.append(post_case("marketNavInputsV3", "/backtest/nav-inputs",
                               {**body, "visibilityMode": "strict-publication"}, status=422))
    elif name == "rules":
        from src.services.thesis_ledger_control import ThesisLedgerControlStore
        from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
        from tests.test_thesis_ledger_public_fees import request

        store = ThesisLedgerControlStore(os.environ["DATABASE_PATH"])
        body = request()
        store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "dg1-rules",
                               "revision": 1, "enabled": True, "routes": [
                                   {"key": body["routeKey"], "targets": [{
                                       field: body["routeTarget"][field]
                                       for field in ("providerId", "upstreamSource")}]}]})
        runtime = ThesisLedgerProviderRuntime(store)
        patches.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: runtime)
        cases.append(post_case("marketPublicFeesV3", "/market/public-fees", body))
        cases.append(post_case("marketPublicFeesV3", "/market/public-fees",
                               {**body, "range": {"start": "2023-08-22", "end": "2023-08-29"}}, status=422))
    elif name == "reference":
        from src.services.thesis_ledger_control import ThesisLedgerControlStore
        from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime
        from data_provider.longbridge_fetcher import LongbridgeFetcher

        os.environ["THESIS_LEDGER_DSA_SECRET_KEY"] = "controlled-data-runtime-key"
        store = ThesisLedgerControlStore(os.environ["DATABASE_PATH"])
        store.save_provider_config("longbridge", {
            "requestId": "dg1-reference", "enabled": True,
            "credentials": {"method": "legacy", "values": {
                "appKey": "controlled-key", "appSecret": "controlled-secret", "accessToken": "controlled-token"}},
        })
        key = {"kind": "data", "market": "HK", "assetType": "STOCK", "capability": "SECURITY_LOT_SIZE"}
        store.apply_policy_v3({"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "dg1-lot",
                               "revision": 1, "enabled": True, "routes": [{"key": key, "targets": [
                                   {"providerId": "longbridge", "upstreamSource": "longbridge"}]}]})
        patches.setattr(LongbridgeFetcher, "_get_static_info", lambda *args: SimpleNamespace(
            symbol="700.HK", board="HKEquity", lot_size=100,
        ))
        runtime = ThesisLedgerProviderRuntime(store)
        patches.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: runtime)
        body = {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "dg1-lot", "symbol": "00700.HK"}
        cases.append(post_case("securityLotSize", "/market/security-lot-size", body))
        cases.append(post_case("securityLotSize", "/market/security-lot-size", {**body, "start": "2026-01-01"},
                               status=422))
    else:
        raise ValueError("未知数据验收场景")
    return cases, patches.undo
