"""ThesisLedger consumer data gateway 的标准输入、输出和错误边界回归。"""

from dataclasses import dataclass

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_provider_runtime import (
    ProviderExecution,
    ThesisLedgerDataGateway,
    ThesisLedgerDataRequest,
    ThesisLedgerGatewayError,
    ThesisLedgerProviderRuntime,
)


@dataclass
class _Quote:
    """最小的统一实时行情 fixture。"""

    open_price: float = 99.0
    high: float = 102.0
    low: float = 98.0
    price: float = 100.0
    pre_close: float = 99.5
    volume: float = 1000.0
    amount: float = 100000.0


@dataclass
class _Chip:
    """最小的完整筹码摘要 fake。"""

    avg_cost: float = 100.0
    profit_ratio: float = 0.5
    cost_70_low: float = 95.0
    cost_70_high: float = 105.0
    concentration_70: float = 0.1
    cost_90_low: float = 90.0
    cost_90_high: float = 110.0
    concentration_90: float = 0.2


def _store(tmp_path, routes, *, revision=1):
    """创建带指定 Effective Policy route 的独立 SQLite store。"""
    store = ThesisLedgerControlStore(str(tmp_path / "stock_analysis.db"))
    store.apply_policy(
        {
            "contractVersion": 1,
            "consumer": "thesis-ledger",
            "requestId": "gateway-test",
            "revision": revision,
            "enabled": True,
            "routes": routes,
        }
    )
    return store


def test_request_normalizes_standard_fields_and_keeps_optional_range_metadata():
    """标准请求应规范 Capability、symbol 和可选范围字段。"""
    request = ThesisLedgerDataRequest(
        capability=" daily_bar ",
        symbol=" 600519.sh ",
        timeframe="1D",
        start="2025-01-01",
        end="2025-01-10",
        limit=30,
        instrument_type="stock",
        parameters={"adjust": "qfq"},
        request_id="request-1",
    )

    assert request.capability == "DAILY_BAR"
    assert request.symbol == "600519.SH"
    assert request.timeframe == "1d"
    assert request.instrument_type == "STOCK"
    assert request.start == "2025-01-01"
    assert request.end == "2025-01-10"
    assert request.limit == 30
    assert request.parameters == {"adjust": "qfq"}
    assert request.request_id == "request-1"


def test_gateway_returns_effective_policy_and_actual_provider_provenance(tmp_path):
    """Gateway 结果应同时携带 Effective revision、route 和真实 Provider。"""
    store = _store(
        tmp_path,
        {"REALTIME_QUOTE": {"STOCK": ["akshare"]}},
        revision=7,
    )

    class _Adapter:
        """返回统一 Quote 的最小 Provider adapter。"""

        def get_realtime_quote(self, symbol, *, source=None):
            """确认 gateway 传递裸 Provider symbol 并返回 fixture。"""
            assert symbol == "600519"
            assert source == "sina"
            return _Quote()

    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(store, adapters={"akshare": _Adapter()})
    )

    result = gateway.fetch(
        {
            "capability": "REALTIME_QUOTE",
            "symbol": "600519.SH",
            "request_id": "request-2",
        }
    )

    assert result.data.price == 100.0
    assert result.provider == "akshare"
    assert result.fallback_used is False
    assert result.route == ("akshare",)
    assert result.attempted_providers == ("akshare",)
    assert result.effective_revision == 7
    assert result.source_desired_revision == 7
    assert result.served_from_cache is False
    assert result.provenance == {
        "provider": "akshare",
        "servedFromCache": False,
        "fallbackUsed": False,
        "attemptedProviders": ["akshare"],
        "effectiveRevision": 7,
        "sourceDesiredRevision": 7,
    }
    assert result.as_dict()["requestId"] == "request-2"

    keyword_result = gateway.fetch(
        capability="REALTIME_QUOTE",
        symbol="600519.SH",
        request_id="request-2b",
    )
    assert keyword_result.provider == "akshare"


def test_gateway_marks_provider_switch_and_preserves_attempted_route(tmp_path):
    """主 Provider 失败时结果应明确记录 fallback 和实际尝试顺序。"""
    store = _store(
        tmp_path,
        {"REALTIME_QUOTE": {"STOCK": ["akshare", "efinance"]}},
    )

    class _TimeoutAdapter:
        """模拟持续超时的主 Provider。"""

        def get_realtime_quote(self, _symbol, *, source=None):
            """触发 runtime 的有界 retry。"""
            raise TimeoutError("timeout")

    class _HealthyAdapter:
        """模拟返回完整 Quote 的 fallback Provider。"""

        def get_realtime_quote(self, _symbol, *, source=None):
            """返回统一 Quote fixture。"""
            return _Quote()

    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(
            store,
            adapters={"akshare": _TimeoutAdapter(), "efinance": _HealthyAdapter()},
        )
    )

    result = gateway.quote("600519.SH", request_id="request-3")

    assert result.provider == "efinance"
    assert result.fallback_used is True
    assert result.route == ("akshare", "efinance")
    assert result.attempted_providers == ("akshare", "efinance")
    assert result.provenance["fallbackUsed"] is True


def test_gateway_exposes_stable_request_correlated_errors(tmp_path):
    """没有 eligible Provider 时应返回稳定 code 和 request identity。"""
    store = _store(tmp_path, {"REALTIME_QUOTE": {"STOCK": []}})
    gateway = ThesisLedgerDataGateway(ThesisLedgerProviderRuntime(store))

    with pytest.raises(ThesisLedgerGatewayError) as raised:
        gateway.fetch(
            ThesisLedgerDataRequest(
                "REALTIME_QUOTE",
                "600519.SH",
                request_id="request-4",
            )
        )

    error = raised.value
    assert error.code == "NO_ELIGIBLE_PROVIDER"
    assert error.detail() == {
        "contractVersion": 1,
        "code": "NO_ELIGIBLE_PROVIDER",
        "message": "没有可执行的 REALTIME_QUOTE/STOCK Provider",
        "requestId": "request-4",
        "diagnosticId": "request-4",
    }


def test_gateway_can_extend_declared_derived_capability_without_migrating_facade():
    """Derived capability 可注册 metadata-preserving handler，供后续迁移复用。"""
    execution = ProviderExecution(
        value={"rsi14": 56.4},
        capability="INDICATOR",
        instrument_type="STOCK",
        provider="akshare",
        fallback_used=False,
        effective_policy={
            "revision": 2,
            "sourceDesiredRevision": 2,
        },
        route=("akshare",),
        attempted_providers=("akshare",),
    )
    gateway = ThesisLedgerDataGateway(
        handlers={"INDICATOR": lambda _request: execution}
    )

    result = gateway.fetch(
        ThesisLedgerDataRequest("INDICATOR", "600519.SH", request_id="request-5")
    )

    assert result.data == {"rsi14": 56.4}
    assert result.provider == "akshare"
    assert result.effective_revision == 2
    assert result.request.capability == "INDICATOR"


def test_gateway_rejects_unknown_legacy_capability_with_stable_error(tmp_path):
    """旧的非显式 Chip capability 不得静默回退到 native manager。"""
    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(
            _store(
                tmp_path=tmp_path,
                routes={},
            )
        )
    )

    with pytest.raises(ThesisLedgerGatewayError) as raised:
        gateway.fetch(
            ThesisLedgerDataRequest("CHIP", "600519.SH", request_id="request-6")
        )

    assert raised.value.code == "unsupported_capability"
    assert raised.value.request.request_id == "request-6"


def test_fastapi_data_contract_uses_applied_effective_policy_for_all_six_capabilities(
    tmp_path, monkeypatch
):
    """通过 HTTP 串起 ControlStore、Effective Policy、Gateway 和 deterministic fakes。"""
    database_path = str(tmp_path / "stock_analysis.db")
    monkeypatch.setenv("DATABASE_PATH", database_path)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    monkeypatch.setenv("RUNTIME_SCHEDULER_SUPPRESS_START", "true")

    from fastapi import FastAPI
    from api.thesis_ledger import router as thesis_ledger_router
    import src.services.thesis_ledger_provider_runtime as runtime_module

    daily_frame = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=90, freq="D"),
            "open": [100 + index * 0.2 for index in range(90)],
            "high": [100.5 + index * 0.2 for index in range(90)],
            "low": [99.5 + index * 0.2 for index in range(90)],
            "close": [100 + index * 0.2 for index in range(90)],
            "volume": [1000 + index for index in range(90)],
            "amount": [(100 + index * 0.2) * (1000 + index) for index in range(90)],
        }
    )
    nav_frame = pd.DataFrame(
        {
            "日期": ["2025-01-01", "2025-01-02"],
            "单位净值": [1.10, 1.12],
        }
    )
    calls: list[tuple[str, str, str, object | None]] = []

    class _Adapter:
        """按 Provider ID 返回 deterministic 数据并记录实际调用顺序。"""

        def __init__(self, provider_id: str):
            self.provider_id = provider_id

        def get_realtime_quote(self, symbol: str, *, source: str | None = None):
            calls.append((self.provider_id, "REALTIME_QUOTE", symbol, source))
            return None if self.provider_id == "akshare" else _Quote()

        def get_daily_data(self, symbol: str, *, days: int):
            calls.append((self.provider_id, "DAILY_BAR", symbol, days))
            return None if self.provider_id == "akshare" else daily_frame.copy()

        def get_fund_nav_history(self, symbol: str):
            calls.append((self.provider_id, "FUND_NAV", symbol, None))
            if self.provider_id == "akshare":
                return pd.DataFrame({"日期": ["2025-01-02"], "其他字段": [1.0]})
            return nav_frame.copy()

        def get_chip_distribution(self, symbol: str):
            calls.append((self.provider_id, "CHIP_SUMMARY", symbol, None))
            return _Chip()

    from src.services.thesis_ledger_control import ThesisLedgerControlStore
    from src.services.thesis_ledger_provider_runtime import (
        ThesisLedgerDataGateway,
        ThesisLedgerProviderRuntime,
    )

    runtime = ThesisLedgerProviderRuntime(
        ThesisLedgerControlStore(database_path),
        adapters={
            "akshare": _Adapter("akshare"),
            "efinance": _Adapter("efinance"),
        },
    )
    monkeypatch.setattr(runtime_module, "get_thesis_ledger_runtime", lambda: runtime)
    gateway = runtime_module.get_thesis_ledger_data_gateway()
    assert isinstance(gateway, ThesisLedgerDataGateway)

    routes = {
        "REALTIME_QUOTE": {"STOCK": ["akshare", "efinance"]},
        "DAILY_BAR": {"STOCK": ["akshare", "efinance"]},
        "FUND_NAV": {"MUTUAL_FUND": ["akshare", "efinance"]},
        "FUND_NAV_HISTORY": {"MUTUAL_FUND": ["akshare", "efinance"]},
        "CHIP_SUMMARY": {"STOCK": ["akshare"]},
    }
    control_headers = {"Authorization": "Bearer control-token"}
    data_headers = {"Authorization": "Bearer data-token"}
    app = FastAPI()
    app.include_router(thesis_ledger_router, prefix="/api/v1")
    client = TestClient(app)

    applied = client.post(
        "/api/v1/thesis-ledger/control/policies/apply",
        headers=control_headers,
        json={
            "contractVersion": 1,
            "consumer": "thesis-ledger",
            "requestId": "closure-04-black-box",
            "revision": 17,
            "enabled": True,
            "routes": routes,
        },
    )
    assert applied.status_code == 200, applied.text

    effective_response = client.get(
        "/api/v1/thesis-ledger/control/policies/effective",
        headers=control_headers,
    )
    assert effective_response.status_code == 200, effective_response.text
    effective = effective_response.json()["projection"]["effective"]
    assert effective["revision"] == 17
    assert effective["sourceDesiredRevision"] == 17
    assert effective["routes"] == routes
    for capability, instrument_type_routes in routes.items():
        for instrument_type, provider_route in instrument_type_routes.items():
            assert effective["routeStatus"][capability][instrument_type][
                "eligibleProviderIds"
            ] == provider_route

    def effective_route(capability: str, instrument_type: str) -> list[str]:
        return effective["routeStatus"][capability][instrument_type][
            "eligibleProviderIds"
        ]

    quote_route = effective_route("REALTIME_QUOTE", "STOCK")
    daily_bar_route = effective_route("DAILY_BAR", "STOCK")
    fund_nav_route = effective_route("FUND_NAV", "MUTUAL_FUND")
    fund_nav_history_route = effective_route("FUND_NAV_HISTORY", "MUTUAL_FUND")
    chip_route = effective_route("CHIP_SUMMARY", "STOCK")

    def get_contract(path: str, **params):
        response = client.get(path, headers=data_headers, params=params)
        assert response.status_code == 200, response.text
        return response.json()

    quote = get_contract("/api/v1/thesis-ledger/market/quote", symbol="600519.SH")
    bars = get_contract(
        "/api/v1/thesis-ledger/market/bars",
        symbol="600519.SH",
        limit=3,
    )
    indicator = get_contract(
        "/api/v1/thesis-ledger/market/indicators/RSI",
        symbol="600519.SH",
    )
    fund_nav = get_contract("/api/v1/thesis-ledger/market/fund-nav", symbol="000001.OF")
    fund_nav_history = get_contract(
        "/api/v1/thesis-ledger/market/fund-nav/history",
        symbol="000001.OF",
        limit=2,
    )
    chip = get_contract("/api/v1/thesis-ledger/market/chip", symbol="600519.SH")

    assert quote["provider"] == quote_route[-1]
    assert quote["fallbackUsed"] is (len(quote_route) > 1)
    assert len(bars) == 3
    assert {row["provider"] for row in bars} == {daily_bar_route[-1]}
    assert {row["fallbackUsed"] for row in bars} == {len(daily_bar_route) > 1}
    assert indicator["provider"] == daily_bar_route[-1]
    assert indicator["fallbackUsed"] is (len(daily_bar_route) > 1)
    assert fund_nav["provider"] == fund_nav_route[-1]
    assert fund_nav["fallbackUsed"] is (len(fund_nav_route) > 1)
    assert {row["provider"] for row in fund_nav_history} == {
        fund_nav_history_route[-1]
    }
    assert {row["fallbackUsed"] for row in fund_nav_history} == {
        len(fund_nav_history_route) > 1
    }
    assert chip["provider"] == chip_route[-1]
    assert chip["fallbackUsed"] is (len(chip_route) > 1)

    def provider_attempts(capability: str) -> list[str]:
        return [provider for provider, item, _, _ in calls if item == capability]

    assert provider_attempts("REALTIME_QUOTE") == quote_route
    assert provider_attempts("DAILY_BAR") == daily_bar_route * 2
    assert provider_attempts("FUND_NAV") == fund_nav_route + fund_nav_history_route
    assert provider_attempts("CHIP_SUMMARY") == chip_route
    assert calls[0] == ("akshare", "REALTIME_QUOTE", "600519", "sina")
    assert all(symbol == "600519" for _, item, symbol, _ in calls if item == "DAILY_BAR")
    assert all(symbol == "000001" for _, item, symbol, _ in calls if item == "FUND_NAV")
    assert calls[-1] == ("akshare", "CHIP_SUMMARY", "600519", None)
