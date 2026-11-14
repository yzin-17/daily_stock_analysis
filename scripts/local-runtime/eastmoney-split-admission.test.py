"""只验证新维护入口；临时库/替身不构成目标准入或实源验证。"""

import base64
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('split_admission_worker', Path(__file__).with_name('eastmoney-split-admission.py'))
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
CONTENT = (Path(__file__).resolve().parents[3] / 'thesis-ledger/docs/evidence/2026-10-02-m22-159516-split-mapping.json').read_bytes()


def request():
    return {'project': 'thesis-ledger-dev', 'symbol': '159516.SZ', 'start': '2026-03-30',
            'end': '2026-07-10', 'digest': worker.DIGEST, 'validHours': 24,
            'recordedBy': 'offline-contract-test', 'mappingBase64': base64.b64encode(CONTENT).decode()}


class SplitAdmissionContracts(unittest.TestCase):
    def test_new_review_packet_is_compatible_with_original_resolver_without_writes(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'daily-stock-analysis'))
        from src.services.thesis_ledger_split_mapping_v3 import resolve_split_mapping_observations
        now = datetime(2026, 10, 12, tzinfo=timezone.utc)
        admission = worker.review_snapshot(request(), now)
        with patch('socket.socket', side_effect=AssertionError('局部合同禁止网络')):
            result = resolve_split_mapping_observations([], evidence_bytes=CONTENT,
                                                        admission=admission, observed_at=now)
        self.assertEqual(result, [])
        self.assertEqual(admission['validUntil'], '2026-10-13T00:00:00+00:00')

    def test_remote_request_rejects_changed_scope_hash_bytes_and_fields(self):
        self.assertEqual(worker.reviewed_request(request()), CONTENT)
        for key, value in [('project', 'production'), ('symbol', '510300.SH'),
                           ('start', '2026-03-27'), ('end', '2026-07-11'), ('digest', '0' * 64),
                           ('validHours', True), ('validHours', 25), ('recordedBy', None),
                           ('mappingBase64', base64.b64encode(b'{}').decode()), ('unknown', 'value')]:
            payload = request(); payload[key] = value
            with self.subTest(key=key, value=value), self.assertRaises((ValueError, TypeError)):
                worker.reviewed_request(payload)

    def test_failure_before_install_or_readback_prevents_record(self):
        calls = []
        evidence = SimpleNamespace(put=lambda _: calls.append('put') or 'sha256:' + worker.DIGEST,
                                   read=lambda _: calls.append('read') or CONTENT)
        control = SimpleNamespace(record_route_admission_v3=lambda **_: calls.append('record'))
        admission = {'validFrom': '2026-10-12T00:00:00+00:00', 'validUntil': '2026-10-13T00:00:00+00:00'}
        def reject(*args, **kwargs):
            raise ValueError('审核拒绝')
        with self.assertRaises(ValueError):
            worker.install_and_record(CONTENT, request(), admission, evidence, control, reject)
        self.assertEqual(calls, [])
        evidence.read = lambda _: b'wrong'
        with self.assertRaises(ValueError):
            worker.install_and_record(CONTENT, request(), admission, evidence, control, lambda *a, **k: None)
        self.assertEqual(calls, ['put'])

    def test_new_orchestration_passes_exact_fields_without_credential_or_policy_port(self):
        calls = []
        evidence = SimpleNamespace(put=lambda _: calls.append('put') or 'sha256:' + worker.DIGEST,
                                   read=lambda _: calls.append('read') or CONTENT)
        def capture(**kwargs):
            calls.append(kwargs)
            return {'recordVersion': 1, 'evidenceSha256': worker.DIGEST}
        control = SimpleNamespace(record_route_admission_v3=capture)
        admission = {'validFrom': '2026-10-12T00:00:00+00:00', 'validUntil': '2026-10-13T00:00:00+00:00'}
        worker.install_and_record(CONTENT, request(), admission, evidence, control, lambda *a, **k: calls.append('validate'))
        self.assertEqual(calls[:3], ['validate', 'put', 'read'])
        fields = calls[-1]
        self.assertEqual(fields['key'], worker.KEY)
        self.assertEqual(fields['target'], worker.TARGET)
        self.assertEqual(fields['scope_symbols'], ['159516.SZ'])
        self.assertEqual((fields['scope_date_from'], fields['scope_date_to']), ('2026-03-30', '2026-07-10'))
        self.assertEqual(fields['credential_revision'], 'not-required')
        self.assertEqual(fields['recorded_by'], 'offline-contract-test')

    def test_constructor_has_no_schema_or_credential_rotation_side_effect(self):
        class ExistingStore:
            def __init__(self, **kwargs):
                raise AssertionError('禁止普通Schema/凭据构造器')
        with patch.dict(sys.modules, {'src.services.thesis_ledger_control': SimpleNamespace(ThesisLedgerControlStore=ExistingStore)}):
            store = worker.admission_only_store('/app/data/stock_analysis.db')
        self.assertIsInstance(store, ExistingStore)
        self.assertEqual(store.database_path, '/app/data/stock_analysis.db')

    def test_parallel_existing_record_is_rejected_inside_original_write_transaction(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / 'synthetic.db'
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE thesis_ledger_route_admission_v3 '
                                   '(consumer TEXT, provider_id TEXT, upstream_source TEXT, route_key_json TEXT)')
                worker.assert_first_admission(connection)
            # 模拟只读预检后其他写者新增；本测试没有生产ControlStore或合法准入数据。
            with sqlite3.connect(database) as other:
                other.execute('INSERT INTO thesis_ledger_route_admission_v3 VALUES (?, ?, ?, ?)',
                              ('thesis-ledger', 'akshare', 'eastmoney', json.dumps(worker.KEY)))
            connection = worker.insert_only_connection(database)
            try:
                with self.assertRaisesRegex(ValueError, '已有拆分准入'):
                    with connection:
                        connection.execute('BEGIN IMMEDIATE')
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.execute('SELECT COUNT(*) FROM thesis_ledger_route_admission_v3').fetchone()[0], 1)
            finally:
                connection.close()

    def test_wrong_or_missing_database_schema_cannot_be_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'missing.db'
            with self.assertRaises(ValueError):
                worker.target_database_path(path)
            with self.assertRaises(sqlite3.OperationalError):
                worker.insert_only_connection(path)
            self.assertFalse(path.exists())
            with sqlite3.connect(path):
                pass
            with self.assertRaises(ValueError):
                worker.target_database_path(path)
            connection = worker.insert_only_connection(path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    with connection:
                        connection.execute('BEGIN IMMEDIATE')
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
