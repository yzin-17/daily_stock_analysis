"""东财现金经过原事件入口/完整分页，复用同一现金合同。"""

from dataclasses import replace

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_registry import CapabilityRegistry
from src.services.thesis_ledger_event_cash_contract_v1 import CONTRACT
from src.services.thesis_ledger_event_v3 import execute_event_request, EventV3Error
from tests.test_thesis_ledger_events_v3 import state
from tests.test_eastmoney_fund_dividend_reader import page


def test_actual_page_reader_uses_two_pages_one_execution_and_no_unneeded_sdk(state, monkeypatch):
    from src.services import thesis_ledger_event_v3 as execution
    from data_provider import eastmoney_fund_dividend_reader as native
    runtime, request, _, _, _ = state
    pages = []
    factories = []
    source = CAPABILITY_SOURCES.source('akshare')
    monkeypatch.setattr(execution, 'CAPABILITY_SOURCES', CapabilityRegistry((
        replace(source, factory=lambda _c: factories.append(True)),)))

    def read_page(_session, year, number):
        pages.append((year, number))
        rows = [['510300', '沪深300', '2025-06-03', '2025-06-04', '0.064', '2025-06-05', '-']]
        return page(number=number, total=2, rows=rows if number == 1 else [['000001', '其他', '2025-06-03', '2025-06-04', '0.1', '2025-06-05', '-']])

    monkeypatch.setattr(native, '_read_page', read_page)
    result = execute_event_request(runtime, request)
    assert pages == [(2025, 1), (2025, 2)] and factories == []
    assert result['facts'][0]['cashAmount'] == '0.064'
    assert result['facts'][0]['availableAt'] != result['facts'][0]['occurredAt']
    assert 'strategyVisibility' not in result['facts'][0]
    assert result['coverage'] == {'complete': False, 'reason': 'historical_coverage_unverified'}
    assert result['providerRevision'].startswith('eastmoney-fund-fh:')
    assert result['providerRevision'] != result['admission']['sourceRevision']
    notice = CAPABILITY_SOURCES.source('official-dividends').bindings[0]
    binding = CAPABILITY_SOURCES.resolve(request['routeKey'], request['routeTarget'])
    assert binding.contract is notice.contract is CONTRACT


@pytest.mark.parametrize('mutation', ['currency', 'amount', 'provider', 'duplicate', 'future'])
def test_bad_facts_rejected_with_single_source_execution(state, mutation):
    runtime, request, read, *_ = state
    fact = {'symbol': request['symbol'], 'market': 'CN', 'instrumentType': 'ETF', 'type': 'CASH_DIVIDEND',
            'effectiveDate': '2025-06-04', 'cashAmount': '0.064', 'currency': 'CNY',
            'occurredAt': '2025-06-04T00:00:00+08:00', 'availableAt': '2025-06-05T00:00:00+08:00',
            'provider': 'akshare', 'providerRevision': 'observation-revision'}
    facts = [fact]
    if mutation == 'currency':
        fact['currency'] = 'invented'
    elif mutation == 'amount':
        fact['cashAmount'] = 0.064
    elif mutation == 'provider':
        fact['provider'] = 'official-dividends'
    elif mutation == 'duplicate':
        facts.append(dict(fact))
    else:
        fact['availableAt'] = '2099-01-01T00:00:00Z'
    read.return_value = {'facts': facts, 'providerRevision': 'observation-revision'}
    with pytest.raises(EventV3Error) as error:
        execute_event_request(runtime, request, reader=read)
    assert error.value.code == 'invalid_response' and read.call_count == 1
