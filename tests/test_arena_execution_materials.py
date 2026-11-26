"""真实原文替换受控事实；缺资料或不匹配时不编造执行资格。"""

import json
import os
import socket

import pytest

from scripts.arena_execution_materials import build_materials, write_package, REVIEW
from src.services.thesis_ledger_historical_execution import ExecutionRequest
from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive, ROOT
from src.services.thesis_ledger_capability_contracts import CapabilityContractError

SOURCE_ROOT = os.environ.get('ARENA_REAL_EXECUTION_SOURCES_ROOT')
pytestmark = pytest.mark.skipif(not SOURCE_ROOT, reason='真实官方原文须显式提供，不能用受控输入代替')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('真实材料离线验证禁止外部调用')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


@pytest.fixture(scope='module')
def package():
    return build_materials(SOURCE_ROOT)


def query(symbol):
    return ExecutionRequest.model_validate({'contractVersion': 1, 'consumer': 'thesis-ledger',
        'requestId': 'real-evidence-offline', 'policyRevision': 1, 'symbol': symbol,
        'asOf': '2026-11-10T05:01:00.000Z',
        'routeKey': {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'HISTORICAL_EXECUTION_CONTEXT'},
        'routeTarget': {'providerId': 'historical-execution-archive', 'upstreamSource': 'qualified-cn-execution', 'routeIndex': 0}})


@pytest.mark.parametrize('symbol,listing,lower,upper', [
    ('588000.SH', '2020-11-16', '1.062', '1.592'), ('510300.SH', '2012-05-28', '3.717', '4.543')])
def test_real_materials_read_by_actual_archive(package, tmp_path, symbol, listing, lower, upper):
    root = tmp_path / 'archive'
    write_package(package, root)
    context = ExecutionArchive(root).read(query(symbol)).context
    assert context.trading.state == 'trading'
    assert context.trading.basis.listing.listedOn == listing
    assert (context.priceRule.lower, context.priceRule.upper) == (lower, upper)
    assert context.fact.executionRules.positionSettlement.sellableAfterTradingDays == 1
    assert context.fact.executionRules.cashSettlement.sellCreditAfterTradingDays == 0
    assert [c.code for c in context.fact.executionRules.statutoryCharges] == ['sseFundHandling']
    assert context.fact.executionRules.statutoryCharges[0].rate == '0.00004'
    assert all('fixture.invalid' not in e.reference and 'controlled' not in e.id for e in context.evidence)
    audit = package['audit'][symbol]
    assert (audit['visibleRows'], audit['excludedFutureRows']) == (122, 119)
    assert all(point['timestamp'] <= '2026-11-10T13:01:00+08:00' for point in package['observations'][symbol]['points'])
    fact = package['calendar']['facts'][0]
    assert fact['provider'] == 'exchange-calendars'
    assert fact['availableAt'].startswith('2026-03-10T')
    assert fact['sessions'] == [{'startMinute': 570, 'endMinute': 690}, {'startMinute': 780, 'endMinute': 900}]


@pytest.mark.parametrize('mutation', ['hash', 'future', 'ratio', 'limit', 'listing', 'fee-missing', 'symbol'])
def test_real_reviewed_input_cannot_silently_change(tmp_path, mutation):
    spec = json.loads(REVIEW.read_text())
    if mutation == 'hash': spec['sources']['product-588000']['sha256'] = '0' * 64
    elif mutation == 'future': spec['sources']['product-588000']['publishedOn'] = '2026-11-11'
    elif mutation == 'ratio': spec['instruments'][1]['ratio'] = '0.20'
    elif mutation == 'limit': spec['instruments'][0]['upper'] = '1.600'
    elif mutation == 'listing': spec['instruments'][1]['listedOn'] = '2012-05-29'
    elif mutation == 'fee-missing': del spec['sources']['clearing-fees']
    elif mutation == 'symbol': spec['instruments'][1]['symbol'] = '510500.SH'
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError): build_materials(SOURCE_ROOT, path)


def test_real_original_tamper_is_rejected(package, tmp_path):
    root = tmp_path / 'archive'
    write_package(package, root)
    digest = package['materials']['588000.SH']['evidence'][0]['sha256']
    (root / 'originals' / digest).write_bytes(b'changed')
    with pytest.raises(CapabilityContractError, match='原文校验'):
        ExecutionArchive(root).read(query('588000.SH'))


def test_real_calendar_failure_has_no_default(package, monkeypatch):
    monkeypatch.setattr('scripts.arena_execution_materials.calendar_contract_response',
                        lambda *args: {'status': 'unavailable'})
    with pytest.raises(ValueError, match='日历不可用'): build_materials(SOURCE_ROOT)


def test_offline_builder_cannot_write_production(package):
    with pytest.raises(ValueError, match='禁止写入生产'): write_package(package, ROOT)


def test_real_modification_time_is_not_backdated(package):
    evidence = package['materials']['588000.SH']['evidence']
    rules = next(e for e in evidence if e['id'] == 'trading-rules')
    assert rules['availableAt'].startswith('2026-11-03T23:59:59.999')
    price = next(e for e in evidence if e['id'] == 'previous-close')
    assert price['availableAt'].startswith('2026-11-09T23:59:59.999')
    assert price['observedAt'].startswith('2026-11-16T')
