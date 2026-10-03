"""净值最新值与历史值共享日期身份校验。"""

from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.thesis_ledger_provider_runtime import (
    ProviderCallError, ThesisLedgerDataRequest, ThesisLedgerProviderRuntime,
)
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from tests.current_data_policy_fixture import apply_data_policy


@pytest.mark.parametrize('value', ['2026-02-30', 'not-a-date', None, pd.NA, pd.NaT,
                                   '2026-01-01trailing', '20260101'])
def test_latest_nav_rejects_invalid_dates_at_provider_boundary(value):
    frame = pd.DataFrame({'日期': [value], '单位净值': [1.2]})
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime._validate_fund_nav(frame)
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('dates', [
    ['2026-01-01', '2026-01-01T00:00:00Z'],
    ['2026-01-01T08:00:00+08:00', '2026-01-01T00:00:00+00:00'],
])
def test_equivalent_date_spellings_are_duplicates(dates):
    frame = pd.DataFrame({'日期': dates, '单位净值': [1.1, 1.2]})
    with pytest.raises(ProviderCallError, match='重复'):
        ThesisLedgerProviderRuntime._validate_fund_nav(frame)


def test_latest_nav_uses_time_order_instead_of_string_order(monkeypatch):
    from api.thesis_ledger import _real_fund_nav

    frame = pd.DataFrame({
        '日期': ['2026-01-02T00:00:00+08:00', '2026-01-01T23:00:00+00:00'],
        '单位净值': [1.1, 1.2],
    })
    ThesisLedgerProviderRuntime._validate_fund_nav(frame)
    gateway = SimpleNamespace(fund_nav=lambda *a, **k: SimpleNamespace(
        data=frame, provider='akshare', fallback_used=False,
    ))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway', lambda: gateway)
    result = _real_fund_nav('000001.OF')
    assert result['unitNav'] == 1.2
    assert result['navDate'] == '2026-01-01T23:00:00+00:00'


def test_invalid_latest_date_falls_back_before_api_serialization(tmp_path):
    store = ThesisLedgerControlStore(str(tmp_path / 'nav.sqlite'))
    apply_data_policy(store, {'FUND_NAV': {'MUTUAL_FUND': ['akshare', 'efinance']}})
    bad = pd.DataFrame({'日期': ['2026-02-30'], '单位净值': [1.1]})
    good = pd.DataFrame({'日期': ['2026-02-28'], '单位净值': [1.2]})
    runtime = ThesisLedgerProviderRuntime(store, adapters={
        'akshare': SimpleNamespace(get_fund_nav_history=lambda _: bad),
        'efinance': SimpleNamespace(get_fund_nav_history=lambda _: good),
    })
    execution = runtime.execute_request(ThesisLedgerDataRequest('FUND_NAV', '000001.OF'))
    result, provider, fallback = execution.value, execution.provider, execution.fallback_used
    assert result is good
    assert provider == 'efinance'
    assert fallback is True


def test_history_preserves_validated_time_order(monkeypatch):
    from api.thesis_ledger import _real_fund_nav_history

    frame = pd.DataFrame({
        '日期': ['2026-01-02T00:00:00+08:00', '2026-01-01T23:00:00+00:00'],
        '单位净值': [1.1, 1.2],
    })
    ThesisLedgerProviderRuntime._validate_fund_nav_history(frame)
    gateway = SimpleNamespace(fund_nav_history=lambda *a, **k: SimpleNamespace(
        data=frame, provider='akshare', fallback_used=False,
    ))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway', lambda: gateway)
    rows = _real_fund_nav_history('000001.OF', None, None, 10)
    assert [r['unitNav'] for r in rows] == [1.1, 1.2]
