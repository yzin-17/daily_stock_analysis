"""统一绑定的真实入口与扩展改动面证明；来源网络使用确定性原文。"""

from dataclasses import replace
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from data_provider.thesis_ledger_sources.tencent import SOURCE
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, DailyQuery, DailyRead, QuoteQuery, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, DailyBinding, ExactKey, DAILY_CONTRACT
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_control import ThesisLedgerControlStore, PROVIDER_MANIFESTS
from src.services.thesis_ledger_market_v3_adapters import iter_market_v3_bar_adapters, iter_market_v3_gated_bar_adapters
from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime, ThesisLedgerDataRequest
from src.services.thesis_ledger_capability_registry import SourceDefinition, QueryBinding, EventBinding


KEY = ExactKey("CN", "ETF", "DAILY_BAR", "none").route_key()
TARGET = {"providerId": "tencent", "upstreamSource": "tencent"}
PRODUCTION_BINDINGS = tuple((target['providerId'], binding) for _, target, binding in CAPABILITY_SOURCES.routes())
QUERY_BINDING_TYPES = {type(binding) for _, binding in PRODUCTION_BINDINGS}
QUERY_EXAMPLES = tuple(next((provider, binding) for provider, binding in PRODUCTION_BINDINGS if type(binding) is kind)
                       for kind in sorted(QUERY_BINDING_TYPES, key=lambda kind: kind.__name__))


def request(**overrides):
    return ThesisLedgerDataRequest("DAILY_BAR", "510300.SH", timeframe="1d", instrument_type="ETF",
                                   adjustment="none", start="2026-09-29", end="2026-09-30", **overrides)


def policy(key=KEY, target=TARGET):
    return {"contractVersion": 3, "consumer": "thesis-ledger", "requestId": "binding-proof",
            "revision": 1, "enabled": True, "routes": [{"key": key, "targets": [target]}]}


def test_generic_query_cannot_bypass_daily_request_validation():
    calls = []
    binding = next(binding for binding in SOURCE.bindings if binding.key.route_key() == KEY)
    source = replace(SOURCE, bindings=(replace(binding, read=lambda query, context: calls.append(query)),), quote_source_aliases=())
    with pytest.raises(CapabilityContractError) as error:
        CapabilityRegistry((source,)).query(DAILY_CONTRACT, DailyQuery.from_request(request()), KEY, TARGET, ReadContext(None))
    assert error.value.code == "unsupported_capability"
    assert calls == []


def test_quote_market_mismatch_rejected_before_upstream():
    calls = []
    binding = next(binding for binding in SOURCE.bindings if binding.key.capability == "REALTIME_QUOTE")
    source = replace(SOURCE, bindings=(replace(binding, read=lambda query, context: calls.append(query)),))
    target = {"providerId": source.provider_id, "upstreamSource": binding.upstream_source}
    with pytest.raises(CapabilityContractError) as error:
        CapabilityRegistry((source,)).quote(QuoteQuery(symbol="AAPL.US", instrument_type="ETF"),
                                         binding.key.route_key(), target, ReadContext(None))
    assert error.value.code == "invalid_request"
    assert calls == []


@pytest.mark.parametrize("change", ["duplicate", "missing-handler", "wrong-operation", "wrong-contract", "unknown-capability"])
def test_bad_registration_rejected(change):
    binding = SOURCE.bindings[0]
    if change == "duplicate":
        bindings = (binding, binding)
    else:
        fields = {"missing-handler": {"read": None}, "wrong-operation": {"operation": "subscribe"},
                  "wrong-contract": {"contract_version": 2},
                  "unknown-capability": {"key": ExactKey("CN", "STOCK", "MYSTERY", "none")}}
        bindings = (replace(binding, **fields[change]),)
    with pytest.raises(ValueError):
        CapabilityRegistry((replace(SOURCE, bindings=bindings, quote_source_aliases=()),))


@pytest.mark.parametrize('provider,binding', PRODUCTION_BINDINGS,
                         ids=[f'{provider}-{binding.key.market}-{binding.key.asset_type}-{binding.key.capability}-{binding.upstream_source}-{binding.key.adjustment}'
                              for provider, binding in PRODUCTION_BINDINGS])
def test_query_only_binding_cannot_claim_subscription_from_wider_contract(monkeypatch, provider, binding):
    calls = []
    contract = replace(binding.contract, operations=('query', 'subscribe'))
    fields = {'operation': 'subscribe', 'read': lambda *_: calls.append('read')}
    if isinstance(binding, (QueryBinding, EventBinding)):
        fields['contract'] = contract
    else:
        # Daily/Quote的ClassVar注解并不提供运行时操作约束。
        monkeypatch.setattr(type(binding), 'contract', contract)
    candidate = replace(binding, **fields)
    source = SourceDefinition(provider, lambda: {}, lambda _: calls.append('factory'), (candidate,))
    with pytest.raises(ValueError):
        CapabilityRegistry((source,))
    assert calls == []


def test_all_production_bindings_still_declare_only_query():
    assert PRODUCTION_BINDINGS
    assert {binding.operation for _, binding in PRODUCTION_BINDINGS} == {'query'}


@pytest.mark.parametrize('provider,binding', QUERY_EXAMPLES, ids=[type(binding).__name__ for _, binding in QUERY_EXAMPLES])
def test_wider_semantic_contract_does_not_grant_source_subscription(monkeypatch, provider, binding):
    contract = replace(binding.contract, operations=('query', 'subscribe'))
    if isinstance(binding, (QueryBinding, EventBinding)):
        candidate = replace(binding, contract=contract)
    else:
        monkeypatch.setattr(type(binding), 'contract', contract)
        candidate = binding
    registry = CapabilityRegistry((SourceDefinition(provider, lambda: {}, lambda _: None, (candidate,)),))
    target = {'providerId': provider, 'upstreamSource': binding.upstream_source}
    assert registry.resolve(binding.key.route_key(), target, 'query') is candidate
    with pytest.raises(CapabilityContractError, match='未实现订阅'):
        registry.resolve(binding.key.route_key(), target, 'subscribe')


@pytest.mark.parametrize('provider,binding', tuple((provider, binding) for provider, binding in PRODUCTION_BINDINGS
                                                  if binding.key.capability != 'DAILY_BAR'))
def test_non_bar_binding_cannot_hide_price_basis_in_exact_key(provider, binding):
    calls = []
    changed_key = replace(binding.key, adjustment='qfq')
    # 原键投影会丢弃该成员，不能以公开形状相等接受非法登记。
    assert changed_key.route_key() == binding.key.route_key()
    candidate = replace(binding, key=changed_key, read=lambda *_: calls.append('read'))
    source = SourceDefinition(provider, lambda: {}, lambda _: calls.append('factory'), (candidate,))
    with pytest.raises(ValueError):
        CapabilityRegistry((source,))
    assert calls == []


def test_inventory_and_revisions_have_one_owner():
    identities = [(tuple(sorted(key.items())), tuple(sorted(target.items()))) for key, target in (
        *iter_market_v3_bar_adapters(), *iter_market_v3_gated_bar_adapters())]
    assert len(identities) == len(set(identities))
    assert CAPABILITY_SOURCES.resolve({**KEY, "assetType": "STOCK", "adjustment": "hfq"}, TARGET) is None
    with pytest.raises(CapabilityContractError, match="未实现订阅"):
        CAPABILITY_SOURCES.resolve(KEY, TARGET, "subscribe")


def test_legacy_quote_probe_default_is_typed_and_unique_without_route_selection():
    from data_provider.thesis_ledger_sources.akshare import SOURCE as AKSHARE
    quotes = [binding for binding in AKSHARE.bindings if binding.key.capability == 'REALTIME_QUOTE'
              and binding.key.asset_type == 'STOCK']
    assert [binding.upstream_source for binding in quotes if binding.legacy_probe_default] == ['sina']
    with pytest.raises(ValueError, match='必须为 bool'):
        CapabilityRegistry((replace(AKSHARE, bindings=(replace(quotes[0], legacy_probe_default='true'),), quote_source_aliases=()),))
    with pytest.raises(ValueError, match='只能有一个'):
        CapabilityRegistry((replace(AKSHARE, bindings=tuple(replace(binding, legacy_probe_default=True) for binding in quotes)),))


def test_source_factory_scope_is_explicit_and_bounded():
    assert SOURCE.factory_scope == "route"
    CapabilityRegistry((replace(SOURCE, factory_scope="provider"),))
    with pytest.raises(ValueError, match="工厂作用域无效"):
        CapabilityRegistry((replace(SOURCE, factory_scope="arbitrary"),))
    with pytest.raises(ValueError, match="工厂缓存条件无效"):
        CapabilityRegistry((replace(SOURCE, require_credential_revision_for_cache="true"),))
    with pytest.raises(ValueError, match="工厂环境凭据条件无效"):
        CapabilityRegistry((replace(SOURCE, allow_empty_environment_credentials="true"),))


@pytest.mark.parametrize("options", [{"interactive_chart": "true"}, {"vendor_param": "x"}, {"target_timeout_seconds": 10.0}])
def test_parameters_validated_before_upstream(options):
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.daily(request(parameters=options), KEY, TARGET, ReadContext(object()))
    assert error.value.code == "invalid_request"


def test_tencent_query_reaches_real_executor_and_standard_http(tmp_path, monkeypatch):
    from api.thesis_ledger_chart_v3 import router
    calls = []

    class Response:
        def __init__(self, variable):
            self.text = variable + "=" + json.dumps({"code": 0, "data": {"sh510300": {"day": [
                ["2026-09-29", "2", "2.1", "2.2", "1.9", "123", "0", "0", "23.45"],
                ["2026-09-30", "2", "2.1", "2.2", "1.9", "123", "0", "0", "23.45"],
            ]}}})

        def raise_for_status(self):
            pass

    def read(url, *, params, timeout, **kwargs):
        calls.append(params["param"])
        return Response(params["_var"])

    monkeypatch.setattr("data_provider.tencent_native_daily.requests.get", read)
    store = ThesisLedgerControlStore(str(tmp_path / "state.sqlite"))
    store.apply_policy_v3(policy())
    runtime = ThesisLedgerProviderRuntime(store)
    monkeypatch.setattr("src.services.thesis_ledger_provider_runtime.get_thesis_ledger_runtime", lambda: runtime)
    monkeypatch.setenv("THESIS_LEDGER_DSA_TOKEN", "synthetic-contract-token")
    monkeypatch.delenv("THESIS_LEDGER_FIXTURE_MODE", raising=False)
    app = FastAPI()
    app.include_router(router)
    payload = {"contractVersion": 3, "requestId": "first-binding-query",
               "symbol": "510300.SH", "routeKey": KEY, "routeTarget": {**TARGET, "routeIndex": 0},
               "start": "2026-09-29", "end": "2026-09-30", "purpose": "interactive-chart"}
    with TestClient(app) as client:
        response = client.post("/thesis-ledger/market/chart-bars", json=payload,
                               headers={"Authorization": "Bearer synthetic-contract-token"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provenance"]["providerId"] == "tencent"
    assert body["bars"][0]["close"] == 2.1
    assert body["bars"][0]["amount"] == 234500
    assert body["coverage"]["requestedStart"] == payload["start"]
    assert len(calls) == 1


@pytest.mark.parametrize("factory_scope", ["route", "provider"])
def test_new_provider_uses_original_runtime_without_supplier_branch(tmp_path, monkeypatch, factory_scope):
    """接入写集仅为本测试的来源定义和隔离注册；Runtime 不接收新分支。"""
    from api.thesis_ledger_chart_v3 import chart_response
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    from src.services.thesis_ledger_daily_units_contract import DailyFieldUnits, DailyUnitsPolicy

    provider = "synthetic-daily-source"
    target = {"providerId": provider, "upstreamSource": "bounded-original"}
    calls = []
    unit_calls = []

    def read(query, context):
        calls.append(query.symbol)
        frame = pd.DataFrame([{"date": "2026-09-29", "open": 2, "high": 3, "low": 1,
                               "close": 2.1, "volume": None, "amount": None}])
        frame.attrs["upstream_source"] = target["upstreamSource"]
        frame.attrs['fixture_units'] = {'quantity': {'unit': 'funds'}, 'money': 'yuan'}
        return DailyRead(frame, 1)

    def project_units(context):
        unit_calls.append(context)
        assert context.metadata == {'fixture_units': {'quantity': {'unit': 'funds'}, 'money': 'yuan'}}
        assert context.key == KEY and not hasattr(context, 'adapter')
        return DailyFieldUnits(volume='fund-unit', amount='CNY')

    def manifest():
        from src.services.provider_manifest import build_provider_manifest
        return build_provider_manifest(provider, "隔离日线来源", {"DAILY_BAR": ("ETF",)},
                                       upstream_sources=((target["upstreamSource"], "隔离原文"),))

    binding = DailyBinding(ExactKey("CN", "ETF", "DAILY_BAR", "none"), target["upstreamSource"],
                           "fixture-adapter-v1", "fixture-protocol-v1", "fixture-protocol-v1", None, read,
                           public_units_policy=DailyUnitsPolicy(('fixture_units',), project_units))
    source = replace(SOURCE, provider_id=provider, manifest=manifest, factory=lambda context: object(),
                     bindings=(binding,), adapter_import=None, factory_scope=factory_scope, quote_source_aliases=())
    registry = CapabilityRegistry((*CAPABILITY_SOURCES.sources, source))
    monkeypatch.setattr(CAPABILITY_SOURCES, "sources", registry.sources)
    monkeypatch.setattr(CAPABILITY_SOURCES, "_bindings", registry._bindings)
    monkeypatch.setitem(PROVIDER_MANIFESTS, provider, manifest())
    store = ThesisLedgerControlStore(str(tmp_path / "new-source.sqlite"))
    # 登记不会为用户创建路由；本地测试独立显式选择。
    assert store.effective_policy_v3() is None
    store.apply_policy_v3(policy(target=target))
    runtime = ThesisLedgerProviderRuntime(store)
    result = runtime.execute_market_bars_v3(request(), KEY, route_target={**target, "routeIndex": 0})
    assert result.provider == provider and result.fallback_used is False
    assert calls == ["510300.SH"]
    assert market_v3_current_route_revisions(KEY, target, manifest())["adapterRevision"] == "fixture-adapter-v1"
    projected = chart_response({"requestId": "new-source", "symbol": "510300.SH", "routeKey": KEY,
                                "routeTarget": {**target, "routeIndex": 0}, "start": "2026-09-29", "end": "2026-09-30"}, result)
    assert projected["provenance"]["providerId"] == provider
    assert projected["bars"][0]["volume"] is None
    assert projected['sourcePriceBasis']['fieldUnits'] == {'volume': 'fund-unit', 'amount': 'CNY'}
    assert len(unit_calls) == 1 and calls == ['510300.SH']
