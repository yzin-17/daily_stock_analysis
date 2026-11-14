"""仅对显式审阅的过期同花顺个股新闻记录执行一小时续验。"""
from datetime import date, datetime, timedelta
import hmac
import json
from pathlib import Path
import re
import sqlite3

from news_admission_extension import (
    canonical_bytes, check_previous_evidence, evidence_path, read_record, record_sha256,
)

RENEWAL_FIELDS = {'renewExisting', 'expectedRecordVersion', 'expectedRecordSha256'}
CHANGED_COLUMNS = {'scope_date_from', 'scope_date_to', 'valid_from', 'valid_until',
                   'evidence_ref', 'evidence_sha256', 'recorded_by', 'record_version', 'recorded_at'}


def validate_renewal_options(payload):
    assert payload['renewExisting'] is True and not payload.get('extendExisting')
    if 'rollRecentWindow' in payload:
        assert payload['rollRecentWindow'] is True, '滚动窗口必须显式启用'
    assert payload['providerId'] == 'hithink' and payload['symbol'] == '600519.SH'
    assert type(payload['validHours']) is int and payload['validHours'] == 1, '续验仅允许一小时'
    assert type(payload['expectedRecordVersion']) is int and payload['expectedRecordVersion'] >= 1
    assert re.fullmatch(r'[a-f0-9]{64}', payload['expectedRecordSha256'])


def validate_previous(previous, payload, key, target, revisions, credential, at):
    validate_renewal_options(payload)
    assert key == {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'NEWS_SEARCH'}
    assert target == {'providerId': 'hithink', 'upstreamSource': 'hithink-news-stock-timeline'}
    assert previous['consumer'] == 'thesis-ledger' and json.loads(previous['route_key_json']) == key
    assert (previous['provider_id'], previous['upstream_source']) == (target['providerId'], target['upstreamSource'])
    assert previous['record_version'] == payload['expectedRecordVersion'], '旧记录版本变化'
    assert hmac.compare_digest(record_sha256(previous), payload['expectedRecordSha256']), '旧记录摘要变化'
    assert previous['status'] == 'admitted' and previous['invalidated_at'] is None and previous['invalidation_reason'] is None
    assert json.loads(previous['scope_symbols_json']) == ['600519.SH']
    assert datetime.fromisoformat(previous['valid_from']) < datetime.fromisoformat(previous['valid_until']) <= at, '只续验已过期记录'
    assert (previous['adapter_revision'], previous['source_revision']) == revisions, '来源版本变化'
    assert previous['credential_revision'] == credential, '凭据版本变化'
    start, end = date.fromisoformat(payload['start']), date.fromisoformat(payload['end'])
    assert start == at.date() - timedelta(days=7) and end == at.date(), '仅最近七日窗口'
    old_start = date.fromisoformat(previous['scope_date_from'])
    old_end = date.fromisoformat(previous['scope_date_to'])
    if payload.get('rollRecentWindow'):
        assert old_end - old_start == timedelta(days=7), '只能滚动既有最近七日范围'
        assert start == old_start + timedelta(days=1) and end == old_end + timedelta(days=1), '滚动窗口只允许整体向前一天'
    else:
        assert old_start <= start <= end <= old_end, '不得扩大原审阅日期范围'
    observed = datetime.fromisoformat(payload['observedAt'].replace('Z', '+00:00'))
    assert observed.tzinfo is not None and timedelta(0) <= at - observed <= timedelta(minutes=15), '续验须重新实读来源'


def assert_updated(previous, current, payload, at, digest):
    assert set(previous) == set(current)
    assert all(previous[k] == current[k] for k in previous.keys() - CHANGED_COLUMNS), '受保护字段变化'
    assert current['record_version'] == previous['record_version'] + 1
    assert (current['scope_date_from'], current['scope_date_to']) == (payload['start'], payload['end'])
    assert (current['valid_from'], current['valid_until']) == (at.isoformat(), (at + timedelta(hours=1)).isoformat())
    assert current['recorded_at'] == at.isoformat() and current['recorded_by'] == payload['recordedBy']
    assert (current['evidence_ref'], current['evidence_sha256']) == ('sha256:' + digest, digest)


def renewal_evidence(previous, payload, at, evidence):
    scope = {'previousAdmission': previous, 'previousAdmissionSha256': record_sha256(previous),
             'renewedAt': at.isoformat()}
    if payload.get('rollRecentWindow'):
        scope['rollRecentWindow'] = True
    return {**evidence, 'scopeRenewal': scope}


def renew_existing_news_admission(database_path, payload, evidence, key, target,
                                 revisions, credential, guard, now):
    validate_renewal_options(payload)
    path = Path(database_path)
    assert path.is_file() and not path.is_symlink()
    directory = path.parent / 'thesis-ledger-route-evidence'
    mode = 'ro' if payload['mode'] == 'verify' else 'rw'
    connection = sqlite3.connect(path.as_uri() + '?mode=' + mode, uri=True,
                                 isolation_level=None, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        if payload['mode'] == 'verify':
            current = read_record(connection, key, target)
            saved_path = evidence_path(directory, current['evidence_sha256'])
            saved = saved_path.read_bytes()
            renewal = json.loads(saved)['scopeRenewal']
            previous, at = renewal['previousAdmission'], datetime.fromisoformat(renewal['renewedAt'])
            validate_previous(previous, payload, key, target, revisions, credential, at)
            check_previous_evidence(directory, previous)
            expected = renewal_evidence(previous, payload, at, evidence)
            assert saved == canonical_bytes(expected), '续验证据变化'
            digest = record_sha256(expected)
            assert guard() is True, '来源启停或凭据变化'
            assert_updated(previous, current, payload, at, digest)
        else:
            assert payload['mode'] == 'apply'
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                previous = read_record(connection, key, target)
                validate_previous(previous, payload, key, target, revisions, credential, now)
                assert guard() is True, '来源启停或凭据变化'
                check_previous_evidence(directory, previous)
                value = renewal_evidence(previous, payload, now, evidence)
                raw = canonical_bytes(value)
                digest = record_sha256(value)
                saved_path = evidence_path(directory, digest)
                if saved_path.exists():
                    assert saved_path.read_bytes() == raw
                else:
                    with saved_path.open('xb') as output:
                        output.write(raw)
                    saved_path.chmod(0o600)
                updated = connection.execute(
                    'UPDATE thesis_ledger_route_admission_v3 SET scope_date_from=?,scope_date_to=?,valid_from=?,valid_until=?,evidence_ref=?,evidence_sha256=?,recorded_by=?,recorded_at=?,record_version=record_version+1 '
                    'WHERE consumer=? AND route_key_json=? AND provider_id=? AND upstream_source=? AND record_version=?',
                    (payload['start'], payload['end'], now.isoformat(), (now + timedelta(hours=1)).isoformat(),
                     'sha256:' + digest, digest, payload['recordedBy'], now.isoformat(), previous['consumer'],
                     previous['route_key_json'], previous['provider_id'], previous['upstream_source'], previous['record_version']))
                assert updated.rowcount == 1, '续验冲突'
                current = read_record(connection, key, target)
                assert_updated(previous, current, payload, now, digest)
        return {'mode': payload['mode'], 'operation': 'renew-expired', 'writes': payload['mode'] == 'apply',
                'providerId': target['providerId'], 'symbol': payload['symbol'],
                'previousRecordVersion': previous['record_version'], 'recordVersion': current['record_version'],
                'scopeDateFrom': current['scope_date_from'], 'scopeDateTo': current['scope_date_to'],
                'validUntil': current['valid_until'], 'evidenceSha256': digest, 'evidencePath': str(saved_path)}
    finally:
        connection.close()
