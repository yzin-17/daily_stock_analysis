"""研报正式维护入口：有界采证、首次准入与只读核验。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import sys
from uuid import uuid4

KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'RESEARCH_REPORT_METADATA'}
TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
VERSION = 'report-metadata-admission-evidence-v1'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def checked_payload(payload):
    fields = {'mode', 'project', 'symbol', 'start', 'end', 'digest', 'validHours', 'recordedBy'}
    if not isinstance(payload, dict) or set(payload) != fields or payload['project'] != 'thesis-ledger-dev':
        raise ValueError('维护请求字段或项目无效')
    if payload['mode'] not in {'collect', 'apply', 'verify'}:
        raise ValueError('执行模式无效')
    from src.services.thesis_ledger_report_metadata_contract import ReportMetadataRequest
    query = ReportMetadataRequest(requestId=str(uuid4()), symbol=payload['symbol'], start=payload['start'],
        end=payload['end'], page=1, pageSize=2, requestBudgetMs=3000)
    if type(payload['validHours']) is not int or not 1 <= payload['validHours'] <= 24:
        raise ValueError('有效期须为1～24整数小时')
    if payload['mode'] != 'collect':
        if not re.fullmatch(r'[a-f0-9]{64}', payload['digest'] or ''):
            raise ValueError('证据摘要无效')
        if not isinstance(payload['recordedBy'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@/-]{0,63}', payload['recordedBy']):
            raise ValueError('记录人无效')
    return query


def first_admission(connection):
    rows = connection.execute('SELECT route_key_json FROM thesis_ledger_route_admission_v3 '
        'WHERE consumer=? AND provider_id=? AND upstream_source=?', ('thesis-ledger', 'akshare', 'eastmoney'))
    if any(json.loads(row[0]) == KEY for row in rows):
        raise ValueError('已有研报准入记录；不覆盖、不续期')


def control_store(database, guard=None):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore

    class GuardedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            result = super().execute(sql, parameters)
            if sql.strip().upper() == 'BEGIN IMMEDIATE':
                first_admission(self)
                guard()
            return result

    class ScopedStore(ThesisLedgerControlStore):
        def __init__(self):
            # 不初始化Schema或凭据；仅使用已核对的既有数据库。
            self.database_path = str(database)

        def _connect(self):
            mode = 'ro' if guard is None else 'rw'
            factory = sqlite3.Connection if guard is None else GuardedConnection
            connection = sqlite3.connect(Path(database).as_uri() + '?mode=' + mode, uri=True,
                timeout=10, isolation_level=None, factory=factory)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            return connection

    return ScopedStore()


def control_state(store):
    from src.services.thesis_ledger_market_v3_revisions import market_v3_current_route_revisions
    provider = next(row for row in store.provider_registry() if row['providerId'] == 'akshare')
    if provider.get('enabled') is not True or provider.get('configured') is not True:
        raise ValueError('原来源未启用或未配置；本入口不变更来源状态')
    revisions = market_v3_current_route_revisions(KEY, TARGET, provider)
    if not revisions or revisions['credentialRevision'] != 'not-required':
        raise ValueError('精确绑定或修订不匹配')
    with store._connect() as connection:
        rows = [dict(row) for row in connection.execute('SELECT * FROM thesis_ledger_provider_config ORDER BY provider_id')]
    policy = store.effective_policy_v3()
    return {'providerConfigurationsSha256': sha256(canonical(rows)).hexdigest(),
            'policyRevision': policy.get('revision') if policy else None, 'revisions': revisions}


def validate_evidence(evidence, payload, state, now, *, fresh):
    from src.services.thesis_ledger_report_metadata_contract import CONTRACT, ReportMetadataRequest, ReportMetadataResult
    if (not isinstance(evidence, dict) or set(evidence) != {'version', 'project', 'key', 'target', 'request',
            'result', 'startedAt', 'completedAt', 'control'} or evidence['version'] != VERSION
            or evidence['project'] != payload['project'] or evidence['key'] != KEY or evidence['target'] != TARGET):
        raise ValueError('证据身份或结构不匹配')
    query = ReportMetadataRequest.model_validate(evidence['request'])
    if any(getattr(query, name) != payload[name] for name in ['symbol', 'start', 'end']):
        raise ValueError('证据与确认范围不匹配')
    if (query.page, query.pageSize, query.requestBudgetMs) != (1, 2, 3000):
        raise ValueError('证据超出批准的单页预算')
    result_payload = evidence['result']
    if isinstance(result_payload, dict):
        version = result_payload.get('metadataVersion')
        if version in ('research-report-metadata-v1', 'research-report-metadata-v2'):
            if version == 'research-report-metadata-v1' and (query.start is None or query.end is None):
                raise ValueError('旧版研报证据要求完整日期窗口')
            result_payload = {**result_payload, 'metadataVersion': 'market-external-report-metadata-v1'}
    result = CONTRACT.correlate_result(query, ReportMetadataResult.model_validate_json(canonical(result_payload)), TARGET)
    if not result.items or result.historyComplete:
        raise ValueError('缺少可验证的单页元数据')
    revisions = state['revisions']
    if result.adapterRevision != revisions['adapterRevision'] or result.sourceRevision != revisions['sourceRevision']:
        raise ValueError('证据适配器或来源修订已变化')
    if evidence['control']['providerConfigurationsSha256'] != state['providerConfigurationsSha256']:
        raise ValueError('采证后来源配置已变化')
    if fresh and evidence['control'] != state:
        raise ValueError('采证后控制状态已变化')
    started = datetime.fromisoformat(evidence['startedAt'])
    completed = datetime.fromisoformat(evidence['completedAt'])
    fetched = datetime.fromisoformat(result.fetchedAt)
    if any(value.tzinfo is None for value in [started, completed, fetched]):
        raise ValueError('证据时刻缺少时区')
    if not started <= fetched <= completed <= now or completed - started > timedelta(seconds=3):
        raise ValueError('证据时钟或请求预算无效')
    if fresh and now - completed > timedelta(minutes=30):
        raise ValueError('证据已超过首次应用的30分钟时限')
    return query, result


def evidence_path(directory, digest):
    if directory.is_symlink():
        raise ValueError('证据目录不得为软链接')
    path = directory / (digest + '.json')
    if path.is_symlink():
        raise ValueError('证据文件不得为软链接')
    return path


def collect(payload, database, directory):
    from src.services.thesis_ledger_capability_contracts import ReadContext
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_report_metadata_contract import CONTRACT
    query = checked_payload(payload)
    store = control_store(database)
    with store._connect() as connection:
        first_admission(connection)
    state = control_state(store)
    started = datetime.now(timezone.utc)
    result = CAPABILITY_SOURCES.query(CONTRACT, query, KEY, TARGET,
        ReadContext(None, timeout_seconds=3, probe=True))
    completed = datetime.now(timezone.utc)
    if control_state(store) != state:
        raise ValueError('读取期间控制状态变化；不保存证据')
    evidence = {'version': VERSION, 'project': payload['project'], 'key': KEY, 'target': TARGET,
        'request': query.model_dump(mode='json'), 'result': result.model_dump(mode='json'),
        'startedAt': started.isoformat(), 'completedAt': completed.isoformat(), 'control': state}
    validate_evidence(evidence, payload, state, completed, fresh=True)
    raw = canonical(evidence)
    digest = sha256(raw).hexdigest()
    path = evidence_path(directory, digest)
    directory.mkdir(mode=0o700, exist_ok=True)
    with path.open('xb') as output:
        output.write(raw)
    path.chmod(0o600)
    if path.read_bytes() != raw:
        raise ValueError('证据读回不符；不得记录准入')
    return {'mode': 'collect', 'evidenceSha256': digest, 'evidencePath': str(path), 'items': len(result.items),
            'references': [item.reference for item in result.items], 'historyComplete': False, 'admissionWritten': False}


def apply_or_verify(payload, database, directory):
    checked_payload(payload)
    digest = payload['digest']
    path = evidence_path(directory, digest)
    if not path.is_file() or path.stat().st_size > 256 * 1024:
        raise ValueError('证据文件不存在或过大')
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != digest:
        raise ValueError('证据原字节摘要变化')
    store = control_store(database)
    state = control_state(store)
    now = datetime.now(timezone.utc)
    query, _result = validate_evidence(json.loads(raw), payload, state, now, fresh=payload['mode'] == 'apply')
    expected = {'scopeSymbols': [query.symbol], 'scopeDateFrom': query.start, 'scopeDateTo': query.end,
        **state['revisions'], 'evidenceRef': 'sha256:' + digest, 'evidenceSha256': digest,
        'recordedBy': payload['recordedBy'], 'recordVersion': 1, 'admissionState': 'admitted'}
    if payload['mode'] == 'verify':
        record = store.get_route_admission_v3(key=KEY, target=TARGET)
        if not record or any(record.get(key) != value for key, value in expected.items()):
            raise ValueError('准入读回与证据不符或已失效')
        if datetime.fromisoformat(record['validUntil']) - datetime.fromisoformat(record['validFrom']) != timedelta(hours=payload['validHours']):
            raise ValueError('准入有效期不匹配')
    else:
        def guard():
            if control_state(store) != state:
                raise ValueError('应用前控制状态变化')
        record = control_store(database, guard).record_route_admission_v3(
            key=KEY, target=TARGET, evidence_ref=expected['evidenceRef'], evidence_sha256=digest,
            scope_symbols=[query.symbol], scope_date_from=query.start, scope_date_to=query.end,
            adapter_revision=state['revisions']['adapterRevision'], source_revision=state['revisions']['sourceRevision'],
            credential_revision=state['revisions']['credentialRevision'], valid_from=now.isoformat(),
            valid_until=(now + timedelta(hours=payload['validHours'])).isoformat(), recorded_by=payload['recordedBy'])
        if record.get('recordVersion') != 1 or record.get('evidenceSha256') != digest:
            raise ValueError('首次准入回执不符；需只读核对，不自动重试')
        record = store.get_route_admission_v3(key=KEY, target=TARGET)
        if not record or any(record.get(key) != value for key, value in expected.items()):
            raise ValueError('首次准入回执不符；需只读核对，不自动重试')
    return {'mode': payload['mode'], 'status': 'admitted', 'recordVersion': 1, 'evidenceSha256': digest,
        'symbol': query.symbol, 'start': query.start, 'end': query.end, 'validUntil': record['validUntil'],
        'policyWritten': False, 'credentialsWritten': False, 'writes': payload['mode'] == 'apply'}


def main(payload):
    checked_payload(payload)
    from src.services.thesis_ledger_control import _database_path
    configured = Path(_database_path())
    database = configured.resolve()
    if database != Path('/app/data/stock_analysis.db') or configured.is_symlink() or not configured.is_file():
        raise ValueError('目标数据库路径或类型不符')
    directory = database.parent / 'thesis-ledger-report-metadata-evidence'
    if payload['mode'] == 'collect':
        return collect(payload, database, directory)
    return apply_or_verify(payload, database, directory)


if __name__ == '__main__':
    print(json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
