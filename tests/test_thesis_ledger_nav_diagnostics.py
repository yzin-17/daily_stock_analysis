"""标准 NAV 诊断：实际工厂、样本范围、源日期事实及缓存隔离。"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.services.thesis_ledger_exact_probe_read import read_exact_probe, probe_sample
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, no_admissions


def key(capability):
    return {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': capability}


@pytest.mark.parametrize('capability', ['FUND_NAV', 'FUND_NAV_HISTORY'])
def test_exact_probe_actual_factory_is_read_only_and_does_not_replace_normal_cache(monkeypatch, store, capability):
    import akshare as ak
    import data_provider.akshare_fetcher as sdk
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    calls, constructors = [], []
    original = sdk.AkshareFetcher
    def build():
        value = original()
        constructors.append(value)
        return value
    monkeypatch.setattr(sdk, 'AkshareFetcher', build)
    def read(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({'净值日期': [(today - timedelta(days=2)).isoformat(), (today - timedelta(days=1)).isoformat()],
                             '单位净值': [1.1, 1.2]})
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', read)
    runtime = ThesisLedgerProviderRuntime(store)
    normal = runtime._adapter('akshare')
    versions = dict(runtime._adapter_versions)
    policy = store.effective_policy_v3()
    snapshot = store.provider_credential_snapshot('akshare')
    result = read_exact_probe(runtime, key(capability), {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}, snapshot)
    assert len(constructors) == 2 and constructors[0] is normal and constructors[1] is not normal
    assert runtime.adapters['akshare'] is normal and runtime._adapter_versions == versions
    assert calls == [{'symbol': '000001', 'indicator': '单位净值走势'}]
    assert result == {'symbol': '000001.OF', 'start': probe_sample(key(capability))[1], 'end': today.isoformat()}
    assert store.effective_policy_v3() == policy and store.provider_credential_snapshot('akshare') == snapshot
    no_admissions(store)


@pytest.mark.parametrize('capability', ['FUND_NAV', 'FUND_NAV_HISTORY'])
def test_future_source_date_outside_history_window_is_not_hidden_in_probe(monkeypatch, store, capability):
    import akshare as ak
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    calls = []
    def read(**kwargs):
        calls.append(True)
        return pd.DataFrame({'净值日期': [(today - timedelta(days=1)).isoformat(), (today + timedelta(days=1)).isoformat()],
                             '单位净值': [1.1, 1.2]})
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', read)
    runtime = ThesisLedgerProviderRuntime(store)
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(runtime, key(capability), {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}, store.provider_credential_snapshot('akshare'))
    assert error.value.code == 'invalid_response' and calls == [True]
    no_admissions(store)


@pytest.mark.parametrize('capability', ['FUND_NAV', 'FUND_NAV_HISTORY'])
def test_probe_empty_sample_window_is_not_healthy(monkeypatch, store, capability):
    import akshare as ak
    day = (datetime.now(ZoneInfo('Asia/Shanghai')).date() - timedelta(days=30)).isoformat()
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', lambda **kwargs: pd.DataFrame({'净值日期': [day], '单位净值': [1.1]}))
    with pytest.raises(ProviderCallError) as error:
        read_exact_probe(ThesisLedgerProviderRuntime(store), key(capability),
            {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}, store.provider_credential_snapshot('akshare'))
    assert error.value.code in {'not_covered', 'insufficient_coverage'}


@pytest.mark.parametrize('capability', ['FUND_NAV', 'FUND_NAV_HISTORY'])
def test_legacy_smoke_resolves_binding_without_routes_or_manual_admission(monkeypatch, store, capability):
    import akshare as ak
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({'净值日期': ['2026-01-01', '2026-01-02'], '单位净值': [1.1, 1.2]})
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', read)
    before = store.effective_policy_v3()
    result = ThesisLedgerProviderRuntime(store).smoke('akshare', capability)
    assert result['status'] == 'healthy' and result['readOnly'] is True
    assert calls == [{'symbol': '000001', 'indicator': '单位净值走势'}]
    assert store.effective_policy_v3() == before
    no_admissions(store)


def test_diagnostic_unknown_provider_does_not_guess_sdk_method():
    from src.services.thesis_ledger_nav_diagnostics import diagnostic_target
    with pytest.raises(ProviderCallError) as error:
        diagnostic_target('unregistered', 'FUND_NAV', ProviderCallError)
    assert error.value.code == 'unsupported_source'


def test_source_reported_bound_keeps_all_rows_before_limit():
    from data_provider.eastmoney_nav_projection import history_nav
    from src.services.thesis_ledger_fund_nav_history_contract_v1 import NavHistoryRequest
    result = history_nav(pd.DataFrame({'日期': ['2026-01-01', '2026-01-02', '2026-01-03'], '单位净值': [1.1, 1.2, 1.3]}),
        NavHistoryRequest(symbol='000001', end='2026-01-02', limit=1), 'akshare', 'eastmoney')
    assert result.points[0].reported_date == '2026-01-02' and result.source_reported_end == '2026-01-03'
