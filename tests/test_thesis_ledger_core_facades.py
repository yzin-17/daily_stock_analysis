"""ThesisLedger core Data Contract facade 的统一 gateway 回归。"""

from dataclasses import dataclass

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.thesis_ledger import router_v3
from src.services.thesis_ledger_control import PROVIDER_MANIFESTS, ThesisLedgerControlStore
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
    """最小的完整筹码摘要 fixture。"""

    avg_cost: float = 100.0
    profit_ratio: float = 0.5
    cost_70_low: float = 95.0
    cost_70_high: float = 105.0
    concentration_70: float = 0.1
    cost_90_low: float = 90.0
    cost_90_high: float = 110.0
    concentration_90: float = 0.2


def _client(monkeypatch) -> TestClient:
    """建立非 fixture 的 ThesisLedger Data Contract client。"""
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "test-token")
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    app = FastAPI()
    app.include_router(router_v3, prefix="/api/v3")
    return TestClient(app)


class _CoreGateway:
    """返回单 Provider fallback 结果并记录四类 facade 请求。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.quote_value = _Quote()
        self.nav_value = pd.DataFrame(
            [{"日期": "2025-01-02", "单位净值": 1.2}]
        )
        self.nav_history_value = pd.DataFrame(
            [
                {"日期": "2025-01-01", "单位净值": 1.1},
                {"日期": "2025-01-02", "单位净值": 1.2},
            ]
        )
        self.chip_value = _Chip()

    @staticmethod
    def _result(capability: str, symbol: str, value):
        """构造真实 Provider provenance 的 gateway result。"""
        request = ThesisLedgerDataRequest(capability, symbol, request_id=f"{capability}-test")
        execution = ProviderExecution(
            value=value,
            capability=capability,
            instrument_type="MUTUAL_FUND" if capability.startswith("FUND") else "STOCK",
            provider="efinance",
            fallback_used=True,
            effective_policy={"revision": 3, "sourceDesiredRevision": 3},
            route=("akshare", "efinance"),
            attempted_providers=("akshare", "efinance"),
        )
        return ThesisLedgerDataGateway._result_from_execution(request, execution)

    def quote(self, symbol: str, **kwargs):
        """返回 fallback Quote。"""
        self.calls.append(("quote", symbol, kwargs))
        return self._result("REALTIME_QUOTE", symbol, self.quote_value)

    def fund_nav(self, symbol: str, **kwargs):
        """返回 fallback Fund NAV。"""
        self.calls.append(("fund_nav", symbol, kwargs))
        return self._result("FUND_NAV", symbol, self.nav_value)

    def fund_nav_history(self, symbol: str, **kwargs):
        """返回不混源的 fallback Fund NAV history。"""
        self.calls.append(("fund_nav_history", symbol, kwargs))
        return self._result("FUND_NAV_HISTORY", symbol, self.nav_history_value)

    def chip_summary(self, symbol: str, **kwargs):
        """返回完整 fallback Chip 摘要。"""
        self.calls.append(("chip_summary", symbol, kwargs))
        return self._result("CHIP_SUMMARY", symbol, self.chip_value)


def test_core_facades_use_gateway_and_preserve_wire_provenance(monkeypatch):
    """四类核心 facade 都应调用 gateway，保留 provider 与 fallback 字段。"""
    client = _client(monkeypatch)
    gateway = _CoreGateway()

    import src.services.thesis_ledger_provider_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "get_thesis_ledger_data_gateway", lambda: gateway)

    def manager_must_not_be_called():
        """证明核心路径不会回退到 native DataFetcherManager。"""
        raise AssertionError("core facade bypassed the ThesisLedger gateway")

    import api.thesis_ledger as contract

    monkeypatch.setattr(contract, "_manager", manager_must_not_be_called, raising=False)
    headers = {
        "authorization": "Bearer test-token",
        "x-request-id": "core-success-request",
    }

    quote = client.get("/api/v3/thesis-ledger/market/quote?symbol=600519.SH", headers=headers)
    nav = client.get("/api/v3/thesis-ledger/market/fund-nav?symbol=000001.OF", headers=headers)
    history = client.get(
        "/api/v3/thesis-ledger/market/fund-nav/history?symbol=000001.OF&limit=2",
        headers=headers,
    )

    assert quote.status_code == 200
    assert quote.json()["provider"] == "efinance"
    assert quote.json()["fallbackUsed"] is True
    assert nav.status_code == 200
    assert nav.json()["provider"] == "efinance"
    assert nav.json()["fallbackUsed"] is True
    assert history.status_code == 200
    assert len(history.json()) == 2
    assert {row["provider"] for row in history.json()} == {"efinance"}
    assert {row["fallbackUsed"] for row in history.json()} == {True}
    assert [call[0] for call in gateway.calls] == [
        "quote",
        "fund_nav",
        "fund_nav_history",
    ]


def test_chip_facade_uses_gateway_without_native_manager(monkeypatch):
    """Chip 必须走 gateway，且保留摘要的实际 provenance。"""
    client = _client(monkeypatch)
    gateway = _CoreGateway()

    import src.services.thesis_ledger_provider_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "get_thesis_ledger_data_gateway", lambda: gateway)

    import api.thesis_ledger as contract

    monkeypatch.setattr(
        contract,
        "_manager",
        lambda: (_ for _ in ()).throw(AssertionError("derived facade bypassed gateway")),
        raising=False,
    )
    headers = {
        "authorization": "Bearer test-token",
        "x-request-id": "derived-request",
    }

    chip = client.get("/api/v3/thesis-ledger/market/chip?symbol=600519.SH", headers=headers)

    assert chip.status_code == 200
    assert chip.json()["provider"] == "efinance"
    assert chip.json()["fallbackUsed"] is True
    assert [call[0] for call in gateway.calls] == ["chip_summary"]
    assert gateway.calls[0][2]["request_id"] == "derived-request"


def test_core_facades_map_no_eligible_gateway_error_to_stable_contract_error(monkeypatch):
    """禁用/未配置/circuit-open 的路由应返回稳定 no_eligible 错误。"""
    client = _client(monkeypatch)

    class _NoEligibleGateway:
        """模拟 Effective Policy 没有 eligible Provider。"""

        @staticmethod
        def _raise(capability, symbol, request_id):
            """抛出 request-correlated stable gateway error。"""
            raise ThesisLedgerGatewayError(
                "NO_ELIGIBLE_PROVIDER",
                "没有可执行 Provider",
                ThesisLedgerDataRequest(capability, symbol, request_id=request_id),
            )

        def quote(self, symbol, **kwargs):
            """模拟 Quote 无可用 Provider。"""
            return self._raise("REALTIME_QUOTE", symbol, kwargs["request_id"])

        def fund_nav(self, symbol, **kwargs):
            """模拟 Fund NAV 无可用 Provider。"""
            return self._raise("FUND_NAV", symbol, kwargs["request_id"])

        def fund_nav_history(self, symbol, **kwargs):
            """模拟 Fund NAV history 无可用 Provider。"""
            return self._raise("FUND_NAV_HISTORY", symbol, kwargs["request_id"])

        def chip_summary(self, symbol, **kwargs):
            """模拟 CHIP_SUMMARY 无可用 Provider。"""
            return self._raise("CHIP_SUMMARY", symbol, kwargs["request_id"])

    import src.services.thesis_ledger_provider_runtime as runtime_module

    monkeypatch.setattr(runtime_module, "get_thesis_ledger_data_gateway", _NoEligibleGateway)
    headers = {
        "authorization": "Bearer test-token",
        "x-request-id": "known-core-request",
    }
    paths = (
        "/api/v3/thesis-ledger/market/quote?symbol=600519.SH",
        "/api/v3/thesis-ledger/market/fund-nav?symbol=000001.OF",
        "/api/v3/thesis-ledger/market/fund-nav/history?symbol=000001.OF",
        "/api/v3/thesis-ledger/market/chip?symbol=600519.SH",
    )

    responses = [client.get(path, headers=headers) for path in paths]

    assert [response.status_code for response in responses] == [503, 503, 503, 503]
    assert {response.json()["detail"]["code"] for response in responses} == {
        "no_eligible_provider"
    }
    assert {
        response.json()["detail"]["requestId"] for response in responses
    } == {"known-core-request"}
    assert {
        response.json()["detail"]["diagnosticId"] for response in responses
    } == {"known-core-request"}


def _policy_store_with_provider_state(tmp_path, *, enabled=True, circuit="closed"):
    """创建指定 Provider enabled/circuit 状态的 Effective Policy store。"""
    store = ThesisLedgerControlStore(str(tmp_path / "stock_analysis.db"))
    store.save_provider_config("akshare", {"enabled": enabled, "settings": {}})
    if circuit != "closed":
        store.record_health(
            "akshare",
            "REALTIME_QUOTE",
            "STOCK",
            state="degraded",
            circuit=circuit,
            consecutive_failures=3,
            error_code="transient_failure",
            upstream_source="eastmoney",
        )
    store.apply_policy_v3(
        {
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "requestId": "state-test",
            "revision": 1,
            "enabled": True,
            "routes": [{
                "key": {"kind": "data", "market": "CN", "assetType": "STOCK",
                        "capability": "REALTIME_QUOTE"},
                "targets": [{"providerId": "akshare", "upstreamSource": "eastmoney"}],
            }],
        }
    )
    return store


@pytest.mark.parametrize(
    ("enabled", "circuit"),
    [(False, "closed"), (True, "open")],
)
def test_gateway_does_not_invoke_disabled_or_circuit_open_provider(
    tmp_path,
    enabled,
    circuit,
):
    """Effective Policy 不 eligible 时 adapter 不应收到上游请求。"""

    class _Adapter:
        """记录不应发生的 Provider 调用。"""

        calls = 0

        def get_realtime_quote(self, _symbol, *, source=None):
            """若被调用则让测试失败。"""
            self.calls += 1
            return _Quote()

    adapter = _Adapter()
    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(
            _policy_store_with_provider_state(
                tmp_path,
                enabled=enabled,
                circuit=circuit,
            ),
            adapters={"akshare": adapter},
        )
    )

    with pytest.raises(ThesisLedgerGatewayError) as raised:
        gateway.quote("600519.SH", request_id="state-request")

    assert raised.value.code == "NO_ELIGIBLE_PROVIDER"
    assert raised.value.request_id == "state-request"
    assert adapter.calls == 0


def test_gateway_does_not_invoke_unconfigured_provider(tmp_path, monkeypatch):
    """manifest 要求凭证但未配置时，Provider 不应收到上游请求。"""

    monkeypatch.setitem(PROVIDER_MANIFESTS["akshare"], "requiresCredential", True)
    store = ThesisLedgerControlStore(str(tmp_path / "stock_analysis.db"))
    store.apply_policy_v3(
        {
            "contractVersion": 3,
            "consumer": "thesis-ledger",
            "requestId": "unconfigured-test",
            "revision": 1,
            "enabled": True,
            "routes": [{
                "key": {"kind": "data", "market": "CN", "assetType": "STOCK",
                        "capability": "REALTIME_QUOTE"},
                "targets": [{"providerId": "akshare", "upstreamSource": "eastmoney"}],
            }],
        }
    )

    class _Adapter:
        """记录不应发生的 Provider 调用。"""

        calls = 0

        def get_realtime_quote(self, _symbol, *, source=None):
            """若被调用则让测试失败。"""
            self.calls += 1
            return _Quote()

    adapter = _Adapter()
    gateway = ThesisLedgerDataGateway(
        ThesisLedgerProviderRuntime(store, adapters={"akshare": adapter})
    )

    with pytest.raises(ThesisLedgerGatewayError) as raised:
        gateway.quote("600519.SH", request_id="unconfigured-request")

    assert raised.value.code == "NO_ELIGIBLE_PROVIDER"
    assert adapter.calls == 0
