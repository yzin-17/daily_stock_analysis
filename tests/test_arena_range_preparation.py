"""真实区间材料的离线保留、覆盖和拒绝验证；不读取模型或比赛服务。"""

import json
import os
from pathlib import Path
import runpy
import shutil
import socket

import pytest
from tests.test_configured_market_routes import store

from scripts.build_arena_range_materials import build_range, write_range
from scripts.collect_arena_range_sources import requests, validate_url, event_notice_requests
from scripts.audit_arena_range_minutes import audit_minutes
from scripts.build_arena_event_absence_review import review as review_events, label_date

maintenance = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/local-runtime/historical-execution-range.py'))
ARTIFACTS = os.environ.get('ARENA_RANGE_ARTIFACTS')
pytestmark = pytest.mark.skipif(not ARTIFACTS, reason='须明确提供已有真实区间资料，不以受控事实替代')


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError('区间离线验证禁止外部调用')
    monkeypatch.setattr(socket.socket, 'connect', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)


@pytest.fixture(scope='module')
def paths():
    root = Path(ARTIFACTS)
    return [root / name for name in ['arena-range-sources-20261116', 'arena-real-execution-20261116-final',
        'arena-range-published-rules-20261116', 'arena-range-materials-20261116-final',
        'arena-range-minutes-20261116/receipt.json']]


def test_official_request_scope_and_first_previous_session():
    days, previous, items = requests('2026-11-02', '2026-11-16')
    assert len(days) == 11 and previous == '2026-10-30' and len(items) == 50
    for item in items:
        validate_url(item['url'])
    notices = event_notice_requests('2026-11-16')
    assert len(notices) == 3 and {x.get('symbol') for x in notices[1:]} == {'588000.SH', '510300.SH'}
    for item in notices: validate_url(item['url'])
    for bad in ['http://www.sse.com.cn/a', 'https://sse.com.cn.evil.invalid/a', 'https://x@www.sse.com.cn/a']:
        with pytest.raises(ValueError): validate_url(bad)


def test_real_published_attachments_resolve_early_visibility(paths):
    package = build_range(paths[0], paths[1], paths[2])
    assert len(package['materials']) == 22
    assert all(x['status'] == 'ready' for x in package['audit'])
    early = build_range(paths[0], paths[1])
    assert [(x['day'], x['symbol']) for x in early['audit'] if x['status'] != 'ready'] == [
        (day, symbol) for day in ['2026-11-02', '2026-11-03'] for symbol in ['588000.SH', '510300.SH']]


def test_qualifies_all_days_and_preserves_prior_archive(paths, tmp_path):
    assert len(maintenance['qualify_range'](paths[3])) == 132
    destination, backup = tmp_path / 'production', tmp_path / 'backup'
    shutil.copytree(paths[1], destination, ignore=shutil.ignore_patterns('audit.json', 'observations.json', 'calendar.json', 'review.json'))
    old = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob('*') if p.is_file() and p.name != 'audit.json'}
    # 生产归档只包含正式载荷，候选审计保存在私有资料目录。
    (destination / 'audit.json').unlink(missing_ok=True)
    result = maintenance['promote_range'](paths[3], destination, backup)
    assert result['status'] == 'loaded'
    assert len(maintenance['qualify_range'](destination)) == 132
    for name, raw in old.items():
        assert (backup / name).read_bytes() == raw
        if name != 'index.json': assert (destination / name).read_bytes() == raw
    with pytest.raises(ValueError): maintenance['promote_range'](paths[3], destination, tmp_path / 'second')


def test_incomplete_or_changed_candidate_never_moves_prior(paths, tmp_path):
    candidate, old, backup = tmp_path / 'candidate', tmp_path / 'prior', tmp_path / 'backup'
    shutil.copytree(paths[3], candidate)
    shutil.copytree(paths[1], old)
    original = (old / 'index.json').read_bytes()
    index = json.loads((candidate / 'index.json').read_bytes())
    index['dailyContexts'].pop(next(iter(index['dailyContexts'])))
    (candidate / 'index.json').write_text(json.dumps(index))
    with pytest.raises(ValueError): maintenance['promote_range'](candidate, old, backup)
    assert not backup.exists() and (old / 'index.json').read_bytes() == original


def test_actual_minutes_and_every_regular_execution_context(paths):
    result = audit_minutes(paths[4], paths[3])
    assert result['regularMinutes'] == result['executionContextsValidated'] == 5280
    assert result['crossSourceOhlcDifferences'] > 0


def test_missing_minute_cannot_pass(paths, tmp_path):
    packet = json.loads(paths[4].read_bytes())
    packet['results'][0]['result']['value']['rows'].pop(1)
    changed = tmp_path / 'receipt.json'
    changed.write_text(json.dumps(packet))
    with pytest.raises(ValueError, match='分钟格点'): audit_minutes(changed, paths[3])


def test_candidate_cannot_overwrite_prior(paths):
    with pytest.raises(ValueError, match='不得覆盖'):
        write_range({}, paths[1], paths[1])


def test_real_event_directory_and_pdf_dates_remain_candidate_without_admission():
    root = Path(ARTIFACTS)
    result = review_events(root / 'arena-range-event-notices-20261116-official-endpoint',
                           root / 'arena-range-event-documents-20261116')
    assert [item['totalRows'] for item in result['indexes']] == [227, 249]
    assert len(result['publications']) == 14 and not result['eventDatesInWindow']
    assert result['status'] == 'candidate' and result['admissionWritten'] is False
    assert max(item['effectiveDate'] for item in result['publications']) == '2026-01-19'


def test_ambiguous_pdf_date_does_not_pick_first_value():
    with pytest.raises(ValueError, match='明确日期'):
        label_date('除息日2026年1月19日除息日2026年11月10日', '除息日')
    assert label_date('权益登记日2019年12月10日权益登记日在册的款项于2019年12月13日划入', '权益登记日') == '2019-12-10'


def test_range_admission_only_updates_exact_record_and_cannot_reapply(store, tmp_path, monkeypatch):
    """仅用受控资格结果验证事务保护；真实资料资格由前面的 132 点测试单独证明。"""
    from datetime import datetime, timedelta, timezone
    from data_provider.thesis_ledger_sources.historical_execution import ADAPTER_REVISION
    m = maintenance['admit_range'].__globals__
    now = datetime.now(timezone.utc)
    store.save_provider_config(m['TARGET']['providerId'], {'requestId': 'offline-enable', 'enabled': True})
    store.record_route_admission_v3(key=m['KEY'], target=m['TARGET'], evidence_ref='sha256:' + 'a' * 64,
        evidence_sha256='a' * 64, scope_symbols=m['SYMBOLS'], scope_date_from='2026-11-10', scope_date_to='2026-11-10',
        adapter_revision='historical-execution-archive-v2', source_revision='archive-v2:' + m['PRIOR_INDEX'],
        credential_revision='not-required', valid_from=now.isoformat(),
        valid_until=(now + timedelta(hours=24)).isoformat(), recorded_by='offline-prior')
    monkeypatch.setitem(m, 'qualify_range', lambda root: [{'controlled': '事务测试'}])
    monkeypatch.setattr('src.services.thesis_ledger_market_v3_revisions.market_v3_current_route_revisions',
        lambda *args: {'adapterRevision': ADAPTER_REVISION, 'sourceRevision': 'archive-v2:' + m['RANGE_INDEX'],
                       'credentialRevision': 'not-required'})
    registry = type(store).provider_registry
    reads = []
    def changing_health(self):
        values = registry(self)
        reads.append(1)
        values[0]['health']['scopes'] = [{'checkedAt': str(len(reads))}]
        return values
    monkeypatch.setattr(type(store), 'provider_registry', changing_health)
    directory = tmp_path / 'evidence'
    directory.mkdir()
    collected = m['admit_range']({'mode': 'collect', 'digest': None}, store.database_path, tmp_path, directory)
    payload = {'mode': 'apply', 'digest': collected['digest']}
    admitted = m['admit_range'](payload, store.database_path, tmp_path, directory)
    assert admitted['record']['recordVersion'] == 2
    assert admitted['record']['scopeDateFrom'] == '2026-11-02'
    assert admitted['record']['scopeDateTo'] == '2026-11-16'
    assert admitted['protectedSha256'] == collected['control']['protectedSha256']
    assert m['admit_range']({**payload, 'mode': 'verify'}, store.database_path, tmp_path, directory)['record'] == admitted['record']
    with pytest.raises(ValueError, match='控制已变化'):
        m['admit_range'](payload, store.database_path, tmp_path, directory)
