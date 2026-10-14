"""持仓诊断实际工厂、完整输入校验、SDK调用及缓存隔离。"""

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from src.services.thesis_ledger_exact_probe_read import read_exact_probe
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerProviderRuntime
from src.services.thesis_ledger_holdings_diagnostics import diagnostic_target
from tests.test_configured_market_routes import store, no_admissions

KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': 'FUND_HOLDINGS'}
TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}


def rows(year, *, bad=False):
    return pd.DataFrame({'股票代码': ['600519'], '股票名称': ['名称'],
                         '季度': [f'{year}年2季度'], '占净值比例': [float('nan') if bad else 8]})


@pytest.mark.parametrize('mode', ['exact', 'smoke', 'draft'])
@pytest.mark.parametrize('empty_current', [False, True])
def test_actual_factory_preserves_probe_isolation_and_annual_calls(monkeypatch, store, mode, empty_current):
    import akshare as ak
    import data_provider.akshare_fetcher as sdk
    year = datetime.now(ZoneInfo('Asia/Shanghai')).year
    calls, factories = [], []
    original = sdk.AkshareFetcher
    def factory():
        adapter = original()
        factories.append(adapter)
        return adapter
    def read(**kwargs):
        calls.append(kwargs)
        queried = int(kwargs['date'])
        if empty_current and queried == year:
            return pd.DataFrame()
        return rows(queried)
    monkeypatch.setattr(sdk, 'AkshareFetcher', factory)
    monkeypatch.setattr(ak, 'fund_portfolio_hold_em', read)
    runtime = ThesisLedgerProviderRuntime(store)
    normal = runtime._adapter('akshare')
    versions = dict(runtime._adapter_versions)
    policy = store.effective_policy_v3()
    snapshot = store.provider_credential_snapshot('akshare')
    health = []
    original_health = store.record_health
    def record(*args, **kwargs):
        health.append(kwargs)
        return original_health(*args, **kwargs)
    monkeypatch.setattr(store, 'record_health', record)
    if mode == 'exact':
        assert read_exact_probe(runtime, KEY, TARGET, snapshot) == {'symbol': '000001.OF'}
    else:
        result = runtime.smoke('akshare', 'FUND_HOLDINGS',
            credential_snapshot=snapshot if mode == 'draft' else None)
        assert result['status'] == 'healthy' and result['readOnly']
    assert calls == [{'symbol': '000001', 'date': str(y)} for y in ([year, year - 1] if empty_current else [year])]
    assert len(factories) == 2 and factories[0] is normal and factories[1] is not normal
    assert runtime.adapters['akshare'] is normal and runtime._adapter_versions == versions
    assert len(health) == (1 if mode == 'smoke' else 0)
    assert store.effective_policy_v3() == policy and store.provider_credential_snapshot('akshare') == snapshot
    no_admissions(store)


@pytest.mark.parametrize('mode', ['exact', 'draft'])
def test_nonempty_invalid_source_rejects_without_prior_year_fetch(monkeypatch, store, mode):
    import akshare as ak
    year = datetime.now(ZoneInfo('Asia/Shanghai')).year
    calls = []
    def read(**kwargs):
        calls.append(kwargs)
        return rows(year, bad=True)
    monkeypatch.setattr(ak, 'fund_portfolio_hold_em', read)
    runtime = ThesisLedgerProviderRuntime(store)
    snapshot = store.provider_credential_snapshot('akshare')
    if mode == 'exact':
        with pytest.raises(ValueError, match='权重超出范围'):
            read_exact_probe(runtime, KEY, TARGET, snapshot)
    else:
        with pytest.raises(ProviderCallError) as error:
            runtime.smoke('akshare', 'FUND_HOLDINGS', credential_snapshot=snapshot)
        assert error.value.code == 'upstream_failure'
    assert len(calls) == 1
    no_admissions(store)


def test_unregistered_diagnostic_source_does_not_guess_sdk_method():
    with pytest.raises(ProviderCallError) as error:
        diagnostic_target('unknown-provider', ProviderCallError)
    assert error.value.code == 'unsupported_source'
