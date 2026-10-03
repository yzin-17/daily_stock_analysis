"""ThesisLedger consumer data gateway 的标准输入、输出和错误边界回归。"""

from dataclasses import dataclass

import pytest
from tests.current_data_policy_fixture import apply_data_policy
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
    apply_data_policy(store, routes, revision=revision)
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
            assert source == "em"
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
    store = ThesisLedgerControlStore(str(tmp_path / "empty-policy.db"))
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
        "contractVersion": 3,
        "code": "NO_ELIGIBLE_PROVIDER",
        "message": "当前 Control 路由策略不可用",
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


def test_fastapi_current_quote_uses_applied_policy_and_exact_target(tmp_path, monkeypatch):
    """通过 HTTP 验证当前 Control Policy 与精确报价执行一致。"""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "stock_analysis.db"))
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "data-token")
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "control-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    store = _store(tmp_path, {"REALTIME_QUOTE": {"STOCK": ["akshare"]}}, revision=17)

    class _Adapter:
        calls = 0

        def get_realtime_quote(self, symbol, *, source=None):
            self.calls += 1
            assert (symbol, source) == ("600519", "em")
            return _Quote()

    adapter = _Adapter()
    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(store, adapters={"akshare": adapter})
    )
    import src.services.thesis_ledger_provider_runtime as runtime_module
    monkeypatch.setattr(runtime_module, "get_thesis_ledger_data_gateway", lambda: gateway)
    from fastapi import FastAPI
    from api.thesis_ledger import router_v3
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    client = TestClient(app)

    effective = client.get(
        "/api/v3/thesis-ledger/control/policies/effective",
        headers={"Authorization": "Bearer control-token"},
    )
    assert effective.status_code == 200
    assert effective.json()["projection"]["effective"]["revision"] == 17
    quote = client.get(
        "/api/v3/thesis-ledger/market/quote?symbol=600519.SH",
        headers={"Authorization": "Bearer data-token"},
    )
    assert quote.status_code == 200, quote.text
    assert quote.json()["version"] == 3
    assert quote.json()["provider"] == "akshare"
    assert adapter.calls == 1
