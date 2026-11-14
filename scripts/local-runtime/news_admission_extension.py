"""对已获准的个股新闻记录提前日期起点，保留有效期及旧证据链。"""
from datetime import date, datetime, timedelta
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import sqlite3


EXTENSION_FIELDS = {'extendExisting', 'expectedRecordVersion', 'expectedRecordSha256'}
CHANGED_COLUMNS = {'scope_date_from', 'evidence_ref', 'evidence_sha256',
                   'recorded_by', 'record_version', 'recorded_at'}


def validate_extension_options(payload):
    assert payload['extendExisting'] is True
    assert payload['providerId'] in {'akshare', 'hithink'} and payload['symbol'] == '600519.SH'
    assert payload['validHours'] is None, '范围扩展不能另设或延长有效期'
    assert type(payload['expectedRecordVersion']) is int and payload['expectedRecordVersion'] >= 1
    assert re.fullmatch(r'[a-f0-9]{64}', payload['expectedRecordSha256'])


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def record_sha256(record):
    return sha256(canonical_bytes(record)).hexdigest()


def read_record(connection, key, target):
    rows = connection.execute(
        "SELECT * FROM thesis_ledger_route_admission_v3 "
        "WHERE consumer='thesis-ledger' AND provider_id=? AND upstream_source=?",
        (target['providerId'], target['upstreamSource']),
    ).fetchall()
    matches = [dict(row) for row in rows if json.loads(row['route_key_json']) == key]
    assert len(matches) == 1, '既有新闻准入身份不唯一或不存在'
    return matches[0]


def validate_record(record, payload, key, target, revisions, credential_revision, now):
    validate_extension_options(payload)
    assert key == {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'NEWS_SEARCH'}
    assert record['consumer'] == 'thesis-ledger' and json.loads(record['route_key_json']) == key
    assert (record['provider_id'], record['upstream_source']) == (target['providerId'], target['upstreamSource'])
    assert record['record_version'] == payload['expectedRecordVersion'], '旧记录版本已变化'
    assert hmac.compare_digest(record_sha256(record), payload['expectedRecordSha256']), '旧记录摘要已变化'
    assert record['status'] == 'admitted' and record['invalidated_at'] is None and record['invalidation_reason'] is None
    assert json.loads(record['scope_symbols_json']) == ['600519.SH'], '不允许改变标的范围'
    assert datetime.fromisoformat(record['valid_from']) <= now < datetime.fromisoformat(record['valid_until']), '既有准入不在有效期内'
    assert (record['adapter_revision'], record['source_revision']) == revisions, '来源版本已变化'
    assert record['credential_revision'] == credential_revision, '凭据版本已变化'
    start = date.fromisoformat(payload['start'])
    assert now.date() - timedelta(days=7) <= start <= now.date(), '只允许最近七日请求窗口'
    assert start < date.fromisoformat(record['scope_date_from']), '只允许提前日期起点'
    assert payload['end'] == record['scope_date_to'], '不允许改变日期截止点'


def evidence_path(directory, digest):
    assert re.fullmatch(r'[a-f0-9]{64}', digest)
    assert not directory.is_symlink()
    path = directory / (digest + '.json')
    assert not path.is_symlink()
    return path


def check_previous_evidence(directory, record):
    digest = record['evidence_sha256']
    assert record['evidence_ref'] == 'sha256:' + digest
    path = evidence_path(directory, digest)
    assert path.is_file() and sha256(path.read_bytes()).hexdigest() == digest, '旧准入证据缺失或变化'


def extension_evidence(evidence, previous):
    return {**evidence, 'scopeExtension': {
        'previousAdmission': previous, 'previousAdmissionSha256': record_sha256(previous),
    }}


def assert_updated(previous, current, payload, digest):
    assert set(previous) == set(current)
    assert all(previous[key] == current[key] for key in previous.keys() - CHANGED_COLUMNS), '受保护准入字段变化'
    assert current['scope_date_from'] == payload['start']
    assert current['record_version'] == previous['record_version'] + 1
    assert current['recorded_by'] == payload['recordedBy']
    assert (current['evidence_ref'], current['evidence_sha256']) == ('sha256:' + digest, digest)


def extend_existing_news_admission(database_path, payload, evidence, key, target,
                                   revisions, credential_revision, guard, now):
    validate_extension_options(payload)
    database_path = Path(database_path)
    assert not database_path.is_symlink() and database_path.is_file()
    directory = database_path.parent / 'thesis-ledger-route-evidence'
    mode = 'ro' if payload['mode'] == 'verify' else 'rw'
    connection = sqlite3.connect(database_path.as_uri() + '?mode=' + mode, uri=True,
                                 isolation_level=None, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        if payload['mode'] == 'verify':
            current = read_record(connection, key, target)
            path = evidence_path(directory, current['evidence_sha256'])
            stored = path.read_bytes()
            previous = json.loads(stored)['scopeExtension']['previousAdmission']
            validate_record(previous, payload, key, target, revisions, credential_revision, now)
            check_previous_evidence(directory, previous)
            raw = canonical_bytes(extension_evidence(evidence, previous))
            digest = sha256(raw).hexdigest()
            assert stored == raw, '扩展证据不匹配'
            assert guard() is True, '新闻来源启停或凭据已变化'
            assert_updated(previous, current, payload, digest)
        else:
            assert payload['mode'] == 'apply'
            with connection:
                connection.execute('BEGIN IMMEDIATE')
                previous = read_record(connection, key, target)
                validate_record(previous, payload, key, target, revisions, credential_revision, now)
                assert guard() is True, '新闻来源启停或凭据已变化'
                check_previous_evidence(directory, previous)
                raw = canonical_bytes(extension_evidence(evidence, previous))
                digest = sha256(raw).hexdigest()
                path = evidence_path(directory, digest)
                if path.exists():
                    assert path.read_bytes() == raw, '同名扩展证据变化'
                else:
                    with path.open('xb') as output:
                        output.write(raw)
                    path.chmod(0o600)
                cursor = connection.execute(
                    "UPDATE thesis_ledger_route_admission_v3 SET scope_date_from=?, evidence_ref=?, "
                    "evidence_sha256=?, recorded_by=?, record_version=record_version+1, recorded_at=? "
                    "WHERE consumer=? AND route_key_json=? AND provider_id=? AND upstream_source=? AND record_version=?",
                    (payload['start'], 'sha256:' + digest, digest, payload['recordedBy'], now.isoformat(),
                     previous['consumer'], previous['route_key_json'], previous['provider_id'],
                     previous['upstream_source'], previous['record_version']),
                )
                assert cursor.rowcount == 1, '既有准入更新冲突'
                current = read_record(connection, key, target)
                assert_updated(previous, current, payload, digest)
        return {'mode': payload['mode'], 'operation': 'extend-start', 'writes': payload['mode'] == 'apply',
                'providerId': target['providerId'], 'symbol': payload['symbol'],
                'previousRecordVersion': previous['record_version'], 'previousRecordSha256': record_sha256(previous),
                'recordVersion': current['record_version'], 'scopeDateFrom': current['scope_date_from'],
                'scopeDateTo': current['scope_date_to'], 'validFrom': current['valid_from'],
                'validUntil': current['valid_until'], 'evidenceSha256': digest, 'evidencePath': str(path)}
    finally:
        connection.close()
