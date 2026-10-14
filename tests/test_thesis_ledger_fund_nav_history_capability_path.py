"""普通 NAV 历史的实际执行、完整校验与原截窗/限量/UTC 顺序。"""

from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import ValidationError

from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_fund_nav_history_contract_v1 import CONTRACT, NavHistoryRequest, NavHistoryResult
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerDataGateway, ThesisLedgerDataRequest, ThesisLedgerGatewayError, ThesisLedgerProviderRuntime
from tests.test_configured_market_routes import store, apply, no_admissions


KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': 'FUND_NAV_HISTORY'}


def configured(store, provider):
    apply(store, KEY, {'providerId': provider, 'upstreamSource': 'eastmoney'})
    return ThesisLedgerProviderRuntime(store)


def test_akshare_actual_factory_filters_window_then_tail(monkeypatch, store):
    import akshare as ak
    calls = []

    def read(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame({'净值日期': ['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04'],
                             '单位净值': [1.1, 1.2, 1.3, 1.4]})
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', read)
    result = configured(store, 'akshare').execute_request(ThesisLedgerDataRequest('FUND_NAV_HISTORY', '000001.OF',
        start='2026-01-01', end='2026-01-03', limit=2))
    assert calls == [{'symbol': '000001', 'indicator': '单位净值走势'}]
    assert isinstance(result.value, NavHistoryResult)
    assert [point.unit_nav for point in result.value.points] == [1.2, 1.3]
    assert result.value.publication_visibility == 'unverified' and result.value.identity_basis == 'request_bound'
    no_admissions(store)


def test_efinance_actual_paging_and_sorting_are_preserved(monkeypatch, store):
    from tests.test_eastmoney_fund_nav import install, page
    calls = install(monkeypatch, [page(['2026-01-03', '2026-01-02']), page(['2026-01-01'])])
    result = configured(store, 'efinance').execute_request(ThesisLedgerDataRequest('FUND_NAV_HISTORY', '000001'))
    assert [call['data']['pageIndex'] for call in calls] == ['1', '2']
    assert [point.reported_date for point in result.value.points] == ['2026-01-01', '2026-01-02', '2026-01-03']
    no_admissions(store)


@pytest.mark.parametrize('rows', [
    [('2026-01-01', -1), ('2026-01-02', 1.2), ('2026-01-03', 1.3)],
    [('2026-01-01', 1.1), ('2026-01-01', 1.2), ('2026-01-03', 1.3)],
    [('2026-01-02', 1.2), ('2026-01-01', 1.1), ('2026-01-03', 1.3)],
])
def test_bad_unselected_rows_cannot_be_hidden_by_window_or_limit(store, rows):
    frame = pd.DataFrame(rows, columns=['日期', '单位净值'])
    runtime = configured(store, 'akshare')
    runtime.adapters['akshare'] = SimpleNamespace(get_fund_nav_history=lambda _: frame)
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_request(ThesisLedgerDataRequest('FUND_NAV_HISTORY', '000001.OF',
            start='2026-01-03', end='2026-01-03', limit=1))
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('changes', [{'start': 'bad'}, {'end': '2026-02-30'},
    {'start': '2026-01-03', 'end': '2026-01-02'}, {'parameters': {'sdkMethod': 'foo'}},
    {'timeframe': '1d'}, {'adjustment': 'none'}])
def test_history_parameters_are_rejected_before_data_read(store, changes):
    calls = []
    runtime = configured(store, 'akshare')
    runtime.adapters['akshare'] = SimpleNamespace(get_fund_nav_history=lambda _: calls.append(True))
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_request(ThesisLedgerDataRequest('FUND_NAV_HISTORY', '000001.OF', **changes))
    assert error.value.code == 'invalid_request' and calls == []


@pytest.mark.parametrize('capability', ['FUND_NAV', 'FUND_NAV_HISTORY'])
def test_sdk_value_error_is_not_mislabeled_as_request_failure(capability):
    from src.services.thesis_ledger_fund_nav_query import read_latest_nav
    from src.services.thesis_ledger_fund_nav_history_query import read_nav_history
    marker = ValueError('SDK parse failure')
    def fail(_):
        raise marker
    method = read_latest_nav if capability == 'FUND_NAV' else read_nav_history
    with pytest.raises(ValueError) as error:
        method(ThesisLedgerDataRequest(capability, '000001.OF'), 'akshare',
               SimpleNamespace(get_fund_nav_history=fail), 'eastmoney', ProviderCallError)
    assert error.value is marker


def test_latest_bad_result_keeps_source_error_code_after_history_processor_fix(store):
    from tests.test_thesis_ledger_fund_nav_capability_path import configured as latest_runtime
    runtime = latest_runtime(store, 'akshare')
    runtime.adapters['akshare'] = SimpleNamespace(get_fund_nav_history=lambda _: pd.DataFrame(
        {'日期': ['2026-01-01'], '单位净值': [-1]}))
    with pytest.raises(ThesisLedgerGatewayError) as error:
        runtime.execute_request(ThesisLedgerDataRequest('FUND_NAV', '000001.OF'))
    assert error.value.code == 'invalid_response'


def test_original_http_retains_limit_before_utc_filter(monkeypatch, store):
    import akshare as ak
    from api.thesis_ledger import _real_fund_nav_history
    # 报告日均在窗口；UTC 在最后限量之后过滤，不从更早行补充。
    frame = pd.DataFrame({'净值日期': ['2026-01-01T00:00:00Z', '2026-01-02T00:00:00+08:00', '2026-01-02T01:00:00Z'],
                          '单位净值': [1.1, 1.2, 1.3]})
    monkeypatch.setattr(ak, 'fund_open_fund_info_em', lambda **kwargs: frame)
    gateway = ThesisLedgerDataGateway(configured(store, 'akshare'))
    monkeypatch.setattr('src.services.thesis_ledger_provider_runtime.get_thesis_ledger_data_gateway', lambda: gateway)
    rows = _real_fund_nav_history('000001.OF', '2026-01-01', '2026-01-02', 2)
    assert [row['unitNav'] for row in rows] == [1.2]
    assert rows[0]['navDate'] == '2026-01-01T16:00:00+00:00'


def test_schema_and_binding_reject_wrong_window_or_mutated_instance():
    assert NavHistoryRequest(symbol='000001', limit=3650).limit == 3650
    assert NavHistoryRequest(symbol='000001', limit=None).limit is None
    for limit in (0, -1, True):
        with pytest.raises(ValidationError):
            NavHistoryRequest(symbol='000001', limit=limit)
    adapter = SimpleNamespace(get_fund_nav_history=lambda _: pd.DataFrame({'日期': ['2026-01-01', '2026-01-02'], '单位净值': [1.1, 1.2]}))
    result = CAPABILITY_SOURCES.query(CONTRACT, NavHistoryRequest(symbol='000001'), KEY,
        {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}, ReadContext(adapter))
    with pytest.raises(ValidationError):
        CONTRACT.validate_result(result.model_copy(update={'points': tuple(reversed(result.points))}))
    with pytest.raises(CapabilityContractError):
        CAPABILITY_SOURCES.query(CONTRACT, NavHistoryRequest(symbol='000001'), {**KEY, 'assetType': 'STOCK'},
            {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}, ReadContext(adapter))
