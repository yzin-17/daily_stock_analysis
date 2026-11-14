"""正式拆分维护入口的目标端；只在显式应用时安装原证据并首次记录准入。"""

import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import sys

DIGEST = 'b1a4f03f94faf27acf95d2bef9d508ec06eabec1280e04c921bb677154f5fe8e'
KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'ETF', 'capability': 'SPLIT_EVENT'}
TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
REVISIONS = {'adapterRevision': 'dsa-eastmoney-fund-split-mapped-v1',
             'sourceRevision': 'eastmoney-fund-cf-pageinfo-json-v1', 'credentialRevision': 'not-required'}


def reviewed_request(payload):
    if not isinstance(payload, dict) or set(payload) != {
        'project', 'symbol', 'start', 'end', 'digest', 'validHours', 'recordedBy', 'mappingBase64',
    }:
        raise ValueError('应用请求字段不完整或含未知字段')
    expected = {'project': 'thesis-ledger-dev', 'symbol': '159516.SZ',
                'start': '2026-03-30', 'end': '2026-07-10', 'digest': DIGEST}
    if any(payload.get(key) != value for key, value in expected.items()):
        raise ValueError('应用请求不是已审阅的精确项目/范围/摘要')
    if type(payload['validHours']) is not int or not 1 <= payload['validHours'] <= 24:
        raise ValueError('必须明确1～24小时有效期')
    if not isinstance(payload['recordedBy'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@/-]{0,63}', payload['recordedBy']):
        raise ValueError('缺少明确审阅记录人')
    if not isinstance(payload['mappingBase64'], str) or len(payload['mappingBase64']) > 2048:
        raise ValueError('映射传输大小无效')
    content = base64.b64decode(payload['mappingBase64'], validate=True)
    if sha256(content).hexdigest() != DIGEST:
        raise ValueError('映射原字节摘要变化')
    return content


def assert_first_admission(connection):
    # 检查精确能力，不依赖JSON文本字段顺序；任何已有记录均需要另行审阅。
    rows = connection.execute(
        "SELECT route_key_json FROM thesis_ledger_route_admission_v3 "
        "WHERE consumer='thesis-ledger' AND provider_id='akshare' AND upstream_source='eastmoney'",
    )
    if any(json.loads(row[0]) == KEY for row in rows):
        raise ValueError('已有拆分准入记录；本入口不覆盖或续期')


class FirstAdmissionConnection(sqlite3.Connection):
    def execute(self, sql, parameters=()):
        cursor = super().execute(sql, parameters)
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            # 在原记录方法的写事务已持锁后再次核对；并发新增也不能走upsert。
            assert_first_admission(self)
        return cursor


def insert_only_connection(database_path):
    connection = sqlite3.connect(Path(database_path).as_uri() + '?mode=rw', uri=True,
                                 timeout=10, isolation_level=None, factory=FirstAdmissionConnection)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def admission_only_store(database_path):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore

    class AdmissionOnlyStore(ThesisLedgerControlStore):
        def __init__(self):
            # 已有结构由只读预检核对；不调用会初始化Schema/轮换凭据的构造器。
            self.database_path = str(database_path)

        def _connect(self):
            return insert_only_connection(database_path)

    return AdmissionOnlyStore()


def install_and_record(content, request, admission, evidence_store, control_store, validate):
    # 复用现有领域resolver；没有证据安装或记录之前必须完成全部映射审核。
    validate([], evidence_bytes=content, admission=admission,
             observed_at=datetime.fromisoformat(admission['validFrom']))
    reference = evidence_store.put(content)
    if reference != 'sha256:' + DIGEST or evidence_store.read(reference) != content:
        raise ValueError('受控证据读回失败；不得记录准入')
    record = control_store.record_route_admission_v3(
        key=KEY, target=TARGET, consumer='thesis-ledger', evidence_ref=reference,
        evidence_sha256=DIGEST, scope_symbols=[request['symbol']],
        scope_date_from=request['start'], scope_date_to=request['end'],
        adapter_revision=REVISIONS['adapterRevision'], source_revision=REVISIONS['sourceRevision'],
        credential_revision=REVISIONS['credentialRevision'], valid_from=admission['validFrom'],
        valid_until=admission['validUntil'], recorded_by=request['recordedBy'],
    )
    if record['recordVersion'] != 1 or record['evidenceSha256'] != DIGEST:
        raise ValueError('首次准入回执异常；需只读核查，不自动重试')
    return record


def target_database_path(configured):
    configured = Path(configured)
    database = configured.resolve()
    if database != Path('/app/data/stock_analysis.db') or not configured.is_file() or configured.is_symlink():
        raise ValueError('既有目标数据库路径/类型不匹配')
    return database


def review_snapshot(payload, now):
    # 仅供原resolver的预安装审核；此对象不是持久准入记录。
    return {'consumer': 'thesis-ledger', 'routeKey': KEY, 'target': TARGET,
            'status': 'admitted', 'admissionState': 'admitted', 'recordVersion': 1,
            'evidenceRef': 'sha256:' + DIGEST, 'evidenceSha256': DIGEST,
            'scopeSymbols': [payload['symbol']], 'scopeDateFrom': payload['start'],
            'scopeDateTo': payload['end'], **REVISIONS,
            'validFrom': now.isoformat(), 'validUntil': (now + timedelta(hours=payload['validHours'])).isoformat(),
            'recordedAt': now.isoformat(), 'invalidatedAt': None, 'invalidationReason': None}


def main(payload):
    content = reviewed_request(payload)
    from src.services.thesis_ledger_control import _database_path
    from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
    from src.services.thesis_ledger_split_mapping_v3 import resolve_split_mapping_observations
    from data_provider.eastmoney_split_capability import REVISIONS as current_revisions

    if current_revisions != REVISIONS:
        raise ValueError('目标拆分适配器修订已变化；需要重新审阅')
    database = target_database_path(_database_path())
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        connection.execute('PRAGMA query_only=ON')
        assert_first_admission(connection)
    admission = review_snapshot(payload, datetime.now(timezone.utc))
    evidence = MappingEvidenceStore(database.parent / 'thesis-ledger-mapping-evidence')
    record = install_and_record(content, payload, admission, evidence,
                                admission_only_store(database), resolve_split_mapping_observations)
    return {'status': 'admitted', 'record': record, 'evidencePath': str(evidence.root / (DIGEST + '.json')),
            'coverageComplete': False, 'policyWritten': False, 'credentialsWritten': False,
            'completeFreeze': '仍受历史覆盖未验证阻断'}


if __name__ == '__main__':
    print(json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
