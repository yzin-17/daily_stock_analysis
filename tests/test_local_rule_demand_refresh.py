"""真实有限 HTTP 解析、SQLite 重开和 Data 链的按需规则覆盖测试。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import threading
import time

import httpx
import pytest

from data_provider.thesis_ledger_sources import local_materials as local
from src.services import local_rule_demand_refresh as demand
from src.services import thesis_ledger_local_material_selection as selection
from src.services.rule_monitor_fetch import document_digest, fetch_document
from src.services.rule_monitor_registry import Source
from src.services.rule_monitor_runtime import RuleMonitorRuntime
from src.services.rule_monitor_store import RuleMonitorStore
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_public_fees_contract_v1 import FeeRequest
from tests import test_thesis_ledger_public_fee_routes as fee_routes

configured_client = fee_routes.configured_client
DATA_HEADERS = fee_routes.DATA_HEADERS

URL = 'https://www.sse.com.cn/controlled-current-fee-guide'
TEXT = '当前受控收费规则正文，所有费项在本次完整资料查询中保持不变。' * 12
HTML = ('<main>' + TEXT + '</main>').encode()


@pytest.fixture
def setup(monkeypatch, tmp_path, request):
    market, asset_type, default_symbol = getattr(request, 'param', ('CN', 'STOCK', '600036.SH'))
    key = {'kind': 'data', 'market': market, 'assetType': asset_type, 'capability': 'HISTORICAL_PUBLIC_FEES'}
    source, binding = local.origin_candidates(key)[0]
    original = json.loads(selection.material_path(source, binding).read_bytes())
    for doc in original['sourceDocuments']:
        doc['url'] = URL
    original['refreshBaseline'] = {URL: document_digest(HTML, 'text/html')['contentHash']}
    path = tmp_path / 'pack.json'
    path.write_text(json.dumps(original), encoding='utf-8')
    monkeypatch.setattr(selection, 'material_path', lambda *_: path)
    monkeypatch.setattr(local, 'origin_candidates', lambda _: [(source, binding)])
    calls, control = [], {'valid': True, 'status': 200, 'body': HTML, 'delay': 0, 'change': None}

    def handle(request):
        calls.append(str(request.url))
        assert 'authorization' not in request.headers
        if control['delay']:
            time.sleep(control['delay'])
        if control['change']:
            control['change']()
        return httpx.Response(control['status'], content=control['body'], headers={'Content-Type': 'text/html'})

    async def fetch(url, **options):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await fetch_document(url, client=client, **options)

    def registry():
        return {source.provider_id + '/' + binding.upstream_source: Source(
            source.provider_id + '/' + binding.upstream_source, market, '受控收费资料', original['datasetId'],
            sha256(path.read_bytes()).hexdigest(), ({'id': 'current', 'title': '现行收费', 'url': URL},))}
    clock = [datetime(2026, 11, 12, 12, tzinfo=timezone.utc).timestamp()]
    store = RuleMonitorStore(tmp_path / 'rule-source-monitor.sqlite', clock=lambda: clock[0])
    runtime = RuleMonitorRuntime(store, registry=registry, fetch=fetch)
    monkeypatch.setattr(demand, 'create_rule_monitor_runtime', lambda _: runtime)
    context = ReadContext(
        object(), database_path=str(tmp_path / 'providers.sqlite'), timeout_seconds=10,
        control_epoch=(1, 1), publish_guard=lambda: control['valid']
    )

    def query(start='2026-11-12', end='2026-11-12', symbol=None):
        return FeeRequest.model_validate({
            'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'demand-rule',
            'symbol': symbol or default_symbol, 'routeKey': key,
            'routeTarget': {'providerId': 'local-materials', 'upstreamSource': 'local-materials', 'routeIndex': 0},
            'range': {'start': start, 'end': end}
        })
    return {
        'path': path, 'original': original, 'calls': calls, 'control': control, 'clock': clock,
        'store': store, 'runtime': runtime, 'context': context, 'query': query, 'key': key, 'registry': registry, 'fetch': fetch
    }


def rows(s):
    with s['store'].connection() as db:
        return [dict(row) for row in db.execute('SELECT * FROM rule_material_coverage_v1')]


def test_complete_empty_changes_advance_coverage_and_reopen_reuses_evidence(setup, monkeypatch):
    s = setup
    before = s['path'].read_bytes()
    result = local.read(s['query'](), s['context'])
    assert result.range.end == '2026-11-12'
    assert s['calls'] == [URL]
    saved = json.loads(rows(s)[0]['material'])
    assert saved['coverage']['end'] == '2026-11-12'
    assert saved['segments'][-1]['charges'] == s['original']['segments'][-1]['charges']
    assert s['path'].read_bytes() == before
    reopened = RuleMonitorRuntime(
        RuleMonitorStore(s['store'].path, clock=lambda: s['clock'][0]), registry=s['registry'], fetch=s['fetch']
    )
    monkeypatch.setattr(demand, 'create_rule_monitor_runtime', lambda _: reopened)
    assert local.read(s['query'](), s['context']).segments == result.segments
    assert len(s['calls']) == 1
    assert reopened.observations.versions('document', URL)


@pytest.mark.parametrize('setup', [
    ('CN', 'ETF', '510300.SH'), ('HK', 'STOCK', '00147.HK'), ('HK', 'ETF', '02800.HK'),
    ('US', 'STOCK', 'GME.US'), ('US', 'ETF', 'SPY.US')
], indirect=True)
def test_other_markets_and_etf_reuse_their_original_fee_parser(setup):
    s = setup
    result = local.read(s['query'](), s['context'])
    assert result.range.end == '2026-11-12'
    assert result.routeKey == s['query']().routeKey
    assert len(s['calls']) == 1
    assert json.loads(rows(s)[0]['material'])['segments'][-1]['charges'] == s['original']['segments'][-1]['charges']


@pytest.mark.parametrize('setup', [('US', 'STOCK', 'GME.US')], indirect=True)
def test_future_is_decided_by_market_date_without_fetching(setup):
    s = setup
    s['clock'][0] = datetime(2026, 11, 12, 4, tzinfo=timezone.utc).timestamp()
    with pytest.raises(CapabilityContractError) as failure:
        local.read(s['query'](), s['context'])
    assert failure.value.diagnostics['ruleRefresh'] == 'requested_window_unverified'
    assert s['calls'] == []


def test_first_observation_or_new_content_never_becomes_empty_by_repeated_fetch(setup):
    s = setup
    material = s['original'].copy()
    material.pop('refreshBaseline')
    s['path'].write_text(json.dumps(material))
    for _ in range(2):
        with pytest.raises(CapabilityContractError) as failure:
            local.read(s['query'](), s['context'])
        assert failure.value.diagnostics['ruleRefresh'] == 'updates_pending'
        s['clock'][0] += 301
    assert len(s['calls']) == 2
    assert rows(s)[0]['material'] is None
    with s['store'].connection() as db:
        assert db.execute('SELECT body FROM version_snapshots WHERE identity=?', (URL,)).fetchone()[0]


def test_untracked_attachment_is_new_material_even_when_index_is_unchanged(setup):
    s = setup
    html = ('<main>' + TEXT + '<a href="/new-fee-table.pdf">收费附件</a></main>').encode()
    s['control']['body'] = html
    pack = s['original'].copy()
    pack['refreshBaseline'] = {URL: document_digest(html, 'text/html')['contentHash']}
    s['path'].write_text(json.dumps(pack))
    with pytest.raises(CapabilityContractError) as failure:
        local.read(s['query'](), s['context'])
    assert failure.value.diagnostics['ruleRefresh'] == 'updates_pending'
    assert 'https://www.sse.com.cn/new-fee-table.pdf' in json.loads(rows(s)[0]['evidence'])['changes']
    assert rows(s)[0]['material'] is None


@pytest.mark.parametrize('status,body', [(429, b''), (200, b''), (200, b'<main>captcha</main>'), (302, HTML)])
def test_failed_or_empty_document_does_not_advance_and_has_cooldown(setup, status, body):
    s = setup
    s['control'].update(status=status, body=body)
    for _ in range(2):
        with pytest.raises(CapabilityContractError):
            local.read(s['query'](), s['context'])
    assert len(s['calls']) == 1
    assert rows(s)[0]['material'] is None
    assert rows(s)[0]['outcome'] == 'rule_refresh_incomplete'


@pytest.mark.parametrize('change', ['control', 'pack'])
def test_late_control_or_pack_change_cannot_publish_coverage(setup, change):
    s = setup
    if change == 'control':
        s['control']['change'] = lambda: s['control'].update(valid=False)
    else:
        s['control']['change'] = lambda: s['path'].write_text(json.dumps({**s['original'], 'datasetId': 'changed'}))
    with pytest.raises(CapabilityContractError):
        local.read(s['query'](), s['context'])
    assert all(row['material'] is None for row in rows(s))


def test_concurrent_misses_only_one_source_check_and_no_future_or_past_guess(setup):
    s = setup
    s['control']['delay'] = 0.1

    def read():
        try:
            return local.read(s['query'](), s['context']).status
        except CapabilityContractError:
            return 'busy'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: read(), range(2)))
    assert 'supported' in results
    assert len(s['calls']) == 1
    for sample in [s['query']('2020-01-01', '2020-01-02'), s['query']('2026-11-13', '2026-11-13')]:
        with pytest.raises(CapabilityContractError):
            local.read(sample, s['context'])
    assert json.loads(rows(s)[0]['material'])['coverage']['end'] == '2026-11-12'


def test_finished_monitor_does_not_allow_duplicate_fetch_before_coverage_commit(setup, monkeypatch):
    s = setup
    finished, release = threading.Event(), threading.Event()
    inspect = s['runtime'].inspect_once

    def pause_after_monitor(*args, **kwargs):
        result = inspect(*args, **kwargs)
        finished.set()
        assert release.wait(2)
        return result
    monkeypatch.setattr(s['runtime'], 'inspect_once', pause_after_monitor)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(local.read, s['query'](), s['context'])
        try:
            assert finished.wait(2)
            with pytest.raises(CapabilityContractError) as failure:
                local.read(s['query'](), s['context'])
            assert failure.value.diagnostics['ruleRefresh'] == 'refresh_in_progress'
        finally:
            release.set()
        assert first.result().status == 'supported'
    assert s['calls'] == [URL]


def test_within_original_range_and_wrong_exchange_do_not_fetch(setup):
    s = setup
    assert local.read(s['query']('2023-08-28', '2023-08-29'), s['context']).status == 'supported'
    with pytest.raises(CapabilityContractError) as failure:
        local.read(s['query'](symbol='000001.SZ'), s['context'])
    assert failure.value.code == 'not_covered'
    assert s['calls'] == []


@pytest.mark.parametrize('facade', [True, False])
def test_actual_data_http_read_refreshes_and_keeps_original_file(setup, configured_client, facade):
    s = setup
    client, runtime, store = configured_client
    target = {'providerId': 'local-materials', 'upstreamSource': 'local-materials'}
    if not facade:
        target = {'providerId': 'official-rules', 'upstreamSource': 'cn-stock-auction-fees'}
    store.apply_policy_v3({
        'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'demand-policy',
        'revision': 1, 'enabled': True, 'routes': [{'key': s['key'], 'targets': [target]}]
    })
    payload = s['query']().model_dump(mode='json')
    payload['routeTarget'] = {**target, 'routeIndex': 0}
    response = client.post('/api/v3/thesis-ledger/market/public-fees', json=payload, headers=DATA_HEADERS)
    assert response.status_code == 200, response.text
    assert response.json()['range']['end'] == '2026-11-12'
    assert len(s['calls']) == 1
    assert json.loads(s['path'].read_text())['coverage']['end'] == '2023-08-29'
