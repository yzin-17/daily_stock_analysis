"""历史空查询可重建正常交易，缺失或矛盾依据仍须拒绝。"""

from copy import deepcopy
from hashlib import sha256
import json
import socket
from types import SimpleNamespace

import pytest

from data_provider.thesis_ledger_sources.historical_execution import ExecutionArchive, inventory, probe, SOURCE
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_historical_reconstruction import DailyExecutionMaterial, reconstruct_context
from tests.historical_reconstruction_sample import sample, write_archive


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('离线重建禁止外部调用')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


def rebuild(material, originals, at='2026-11-10T05:01:00.000Z'):
    return reconstruct_context(DailyExecutionMaterial.model_validate(material), at, originals.__getitem__)


def replace_response(material, originals, edit):
    page = material['pages'][0]
    body = json.loads(originals[page['sha256']])
    edit(body)
    raw = json.dumps(body).encode()
    page['sha256'] = sha256(raw).hexdigest()
    originals[page['sha256']] = raw


@pytest.mark.parametrize('symbol', ['588000.SH', '510300.SH'])
def test_captured_official_empty_query_through_production_archive(tmp_path, symbol):
    query, material, originals = sample(symbol)
    write_archive(tmp_path, query, material, originals)
    archive = ExecutionArchive(tmp_path)
    result = CAPABILITY_SOURCES.query(SOURCE.bindings[0].contract, query,
        query.routeKey.model_dump(), query.routeTarget.model_dump(), ReadContext(archive))
    context = result.context
    assert context.trading.state == 'trading'
    assert context.trading.intradayHalts == []
    assert context.trading.basis.query.total == 0
    assert context.trading.basis.query.symbol == symbol
    assert context.evidence[-1].observedAt == material['pages'][0]['observedAt']
    assert 'availableAt' not in context.evidence[-1].model_dump()
    later = archive.read(query.model_copy(update={'asOf': '2026-11-10T05:02:00.000Z'}))
    assert later.context.asOf == '2026-11-10T05:02:00.000Z'
    assert later.context.trading.state == 'trading'  # 无需每分钟预制一个快照。
    assert SOURCE.default_enabled is False
    assert all(b.admission_required for b in SOURCE.bindings)


@pytest.mark.parametrize('mutation', ['http-error', 'wrong-symbol', 'wrong-day', 'extra-filter',
    'missing-page', 'wrong-response', 'query-error', 'not-listed', 'delisted', 'no-session',
    'overlap-session', 'lunch', 'future-rule', 'missing-fees', 'naive-session', 'future-query',
    'false-total', 'missing-listing-ref', 'wrong-asset', 'missing-session-ref',
    'response-page', 'response-size', 'not-paginated', 'non-object'])
def test_invalid_reconstruction_still_rejected(mutation):
    _, material, originals = sample()
    page = material['pages'][0]
    at = '2026-11-10T05:01:00.000Z'
    if mutation == 'http-error': page['httpStatus'] = 503
    elif mutation == 'wrong-symbol': page['reference'] = page['reference'].replace('secCode=588000', 'secCode=510300')
    elif mutation == 'wrong-day': page['reference'] = page['reference'].replace('endDate=20261110', 'endDate=20261109')
    elif mutation == 'extra-filter': page['reference'] += '&hiddenFilter=yes'
    elif mutation == 'missing-page':
        replace_response(material, originals, lambda b: b['pageHelp'].update(total=26, pageCount=2))
    elif mutation == 'wrong-response': replace_response(material, originals, lambda b: b.update(sqlId='OTHER'))
    elif mutation == 'query-error': replace_response(material, originals, lambda b: b.update(actionErrors=['denied']))
    elif mutation == 'not-listed': material['listing']['listedOn'] = '2026-11-11'
    elif mutation == 'delisted': material['listing']['delistedOn'] = '2026-11-10'
    elif mutation == 'no-session': material['sessions'] = []
    elif mutation == 'overlap-session': material['sessions'].append(deepcopy(material['sessions'][0]))
    elif mutation == 'lunch': at = '2026-11-10T04:00:00Z'
    elif mutation == 'future-rule': material['evidence'][0]['availableAt'] = '2026-11-11T00:00:00Z'
    elif mutation == 'missing-fees': material['ruleCoverage']['statutoryCharges'] = False
    elif mutation == 'naive-session': material['sessions'][0]['startAt'] = '2026-11-10T05:00:00'
    elif mutation == 'future-query': page['observedAt'] = '2026-11-10T05:00:00Z'
    elif mutation == 'false-total': replace_response(material, originals, lambda b: b['pageHelp'].update(total=False))
    elif mutation == 'missing-listing-ref': material['listing']['evidenceRefs'] = ['missing']
    elif mutation == 'wrong-asset': material['fact']['instrumentType'] = 'STOCK'
    elif mutation == 'missing-session-ref': material['sessions'][0]['evidenceRefs'] = ['missing']
    elif mutation == 'response-page': replace_response(material, originals, lambda b: b['pageHelp'].update(pageNo=2))
    elif mutation == 'response-size': replace_response(material, originals, lambda b: b['pageHelp'].update(pageSize=1))
    elif mutation == 'not-paginated': replace_response(material, originals, lambda b: b.update(isPagination='false'))
    elif mutation == 'non-object':
        page['sha256'] = sha256(b'[]').hexdigest()
        originals[page['sha256']] = b'[]'
    with pytest.raises((ValueError, KeyError)):
        rebuild(material, originals, at)


def with_row(material, originals, **changes):
    row = {'secCode': material['fact']['symbol'].split('.')[0], 'dateSource': '2',
           'startStopDate': '20261110', 'endStopDate': '20261110',
           'startStopType': 'LSTP', 'stopTime': '915', **changes}
    def edit(body):
        body['result'] = [row]
        body['pageHelp'].update(data=[row], total=1, pageCount=1)
    replace_response(material, originals, edit)


def test_precise_halt_reconstructs_then_resumes_without_future_end_leak():
    _, material, originals = sample()
    material['sessions'].insert(0, {'startAt': '2026-11-10T01:30:00Z',
        'endAt': '2026-11-10T03:30:00Z', 'evidenceRefs': ['rules']})
    with_row(material, originals)
    morning = rebuild(material, originals, '2026-11-10T02:00:00Z')
    assert morning.trading.state == 'suspended'
    assert morning.trading.intradayHalts[0].endedAt is None
    assert rebuild(material, originals).trading.state == 'trading'


@pytest.mark.parametrize('at', ['2026-11-10T03:30:00Z', '2026-11-10T07:00:00Z'])
def test_interval_end_minute_has_security_state_without_extending_session(at):
    _, material, originals = sample()
    material['sessions'].insert(0, {'startAt': '2026-11-10T01:30:00Z',
        'endAt': '2026-11-10T03:30:00Z', 'evidenceRefs': ['rules']})
    result = rebuild(material, originals, at)
    assert result.trading.state == 'trading'
    assert result.trading.basis.session.endAt == at
    from datetime import datetime, timedelta
    after = (datetime.fromisoformat(at.replace('Z', '+00:00')) + timedelta(microseconds=1)).isoformat()
    with pytest.raises(ValueError, match='唯一的目标交易会话'):
        rebuild(material, originals, after)


@pytest.mark.parametrize('changes', [{'dateSource': '4'}, {'stopTime': '13'},
    {'secCode': '510300'}, {'endStopDate': ''}])
def test_ambiguous_or_mismatched_relevant_halt_rejected(changes):
    _, material, originals = sample()
    with_row(material, originals, **changes)
    with pytest.raises(ValueError): rebuild(material, originals)


@pytest.mark.parametrize('source', ['1', '3'])
def test_creation_redemption_suspension_does_not_block_secondary_trading(source):
    _, material, originals = sample()
    with_row(material, originals, dateSource=source)
    assert rebuild(material, originals).trading.state == 'trading'


def test_daily_probe_tamper_and_revision_guards(monkeypatch, tmp_path):
    query, material, originals = sample()
    write_archive(tmp_path, query, material, originals)
    monkeypatch.setattr('data_provider.thesis_ledger_sources.historical_execution.ROOT', tmp_path)
    monkeypatch.setattr('data_provider.thesis_ledger_sources.historical_execution.REVISION', inventory(tmp_path))
    result = probe(SimpleNamespace(key=query.routeKey.model_dump(), target={k: v for k, v in
        query.routeTarget.model_dump().items() if k != 'routeIndex'}, policy_revision=1))
    assert result['checkedContexts'] == 1
    archive = ExecutionArchive(tmp_path)
    (tmp_path / 'originals' / material['pages'][0]['sha256']).write_bytes(b'changed')
    with pytest.raises(CapabilityContractError, match='原文校验'): archive.read(query)
    (tmp_path / 'index.json').write_text('{}')
    with pytest.raises(CapabilityContractError) as error: archive.read(query)
    assert error.value.code == 'admission_invalid'


def test_complete_multi_page_query_is_accepted_but_duplicate_page_is_not():
    _, material, originals = sample()
    page = material['pages'][0]
    page['reference'] = page['reference'].replace('pageHelp.pageSize=25', 'pageHelp.pageSize=1')
    body = json.loads(originals[page['sha256']])
    rows = [{'secCode': '588000', 'dateSource': source} for source in ['1', '3']]
    material['pages'] = []
    for i, row in enumerate(rows, 1):
        response = deepcopy(body)
        response['result'] = [row]
        response['pageHelp'].update(data=[row], total=2, pageCount=2, pageNo=i, pageSize=1)
        raw = json.dumps(response).encode()
        digest = sha256(raw).hexdigest()
        originals[digest] = raw
        material['pages'].append({**page, 'id': f'page-{i}', 'sha256': digest,
            'reference': page['reference'].replace('pageHelp.pageNo=1', f'pageHelp.pageNo={i}')})
    assert rebuild(material, originals).trading.basis.query.pages == 2
    second = material['pages'][1]
    response = json.loads(originals[second['sha256']])
    response['result'] = response['pageHelp']['data'] = [rows[0]]
    raw = json.dumps(response).encode()
    second['sha256'] = sha256(raw).hexdigest()
    originals[second['sha256']] = raw
    with pytest.raises(ValueError, match='重复记录'): rebuild(material, originals)
