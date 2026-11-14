"""研报正式准入的隔离测试；真实SQLite往返，不连接外部来源或Docker。"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from data_provider.eastmoney_report_metadata import project_page
from src.services.thesis_ledger_control import ThesisLedgerControlStore
from src.services.thesis_ledger_report_metadata_contract_v1 import ReportMetadataRequest
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES

spec = importlib.util.spec_from_file_location('report_admission', Path(__file__).with_name('report-metadata-admission.py'))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def payload(mode='apply'):
    return {'mode': mode, 'project': 'thesis-ledger-dev', 'symbol': '600519.SH', 'start': '2026-08-01',
            'end': '2026-10-23', 'digest': None, 'validHours': 24, 'recordedBy': 'codex-local-research'}


def page(query, fetched):
    rows = [{'stockCode': '600519', 'infoCode': source, 'publishDate': date + ' 00:00:00',
             'title': '隔离验证研报', 'orgSName': '测试机构'} for source, date in
            [('AP202609211829719676', '2026-09-21'), ('AP202608211828244348', '2026-08-21')]]
    return project_page({'hits': 2, 'size': 2, 'TotalPage': 1, 'pageNo': 1, 'data': rows}, query, fetched.isoformat())


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'control.db'
        ThesisLedgerControlStore(str(self.database))
        self.directory = Path(self.temp.name) / 'evidence'
        self.state = worker.control_state(worker.control_store(self.database))

    def evidence(self, now=None):
        request = payload()
        now = now or datetime.now(timezone.utc)
        query = ReportMetadataRequest(requestId='isolated-evidence', symbol=request['symbol'],
            start=request['start'], end=request['end'], page=1, pageSize=2, requestBudgetMs=3000)
        value = {'version': worker.VERSION, 'project': request['project'], 'key': worker.KEY, 'target': worker.TARGET,
            'request': query.model_dump(mode='json'), 'result': page(query, now - timedelta(milliseconds=50)).model_dump(mode='json'),
            'startedAt': (now - timedelta(milliseconds=100)).isoformat(),
            'completedAt': (now - timedelta(milliseconds=10)).isoformat(), 'control': deepcopy(self.state)}
        return request, value

    def save(self, request, value):
        raw = worker.canonical(value)
        digest = worker.sha256(raw).hexdigest()
        self.directory.mkdir(exist_ok=True)
        (self.directory / (digest + '.json')).write_bytes(raw)
        return {**request, 'digest': digest}

    def no_admission(self):
        self.assertIsNone(worker.control_store(self.database).get_route_admission_v3(key=worker.KEY, target=worker.TARGET))

    def test_real_sqlite_first_record_verify_and_no_overwrite(self):
        request, value = self.evidence()
        request = self.save(request, value)
        before = worker.control_state(worker.control_store(self.database))
        receipt = worker.apply_or_verify(request, self.database, self.directory)
        self.assertEqual(receipt['recordVersion'], 1)
        self.assertEqual(before, worker.control_state(worker.control_store(self.database)))
        self.assertFalse(worker.apply_or_verify({**request, 'mode': 'verify'}, self.database, self.directory)['writes'])
        with self.assertRaisesRegex(ValueError, '不覆盖'):
            worker.apply_or_verify(request, self.database, self.directory)
        self.assertEqual(worker.control_store(self.database).get_route_admission_v3(key=worker.KEY, target=worker.TARGET)['recordVersion'], 1)

    def test_collect_one_request_writes_only_minimal_evidence(self):
        def read(_contract, query, key, target, context):
            self.assertTrue(context.probe)
            self.assertEqual(context.timeout_seconds, 3)
            return page(query, datetime.now(timezone.utc))
        with patch.object(CAPABILITY_SOURCES, 'query', side_effect=read) as consume:
            receipt = worker.collect(payload('collect'), self.database, self.directory)
        self.assertEqual(consume.call_count, 1)
        raw = Path(receipt['evidencePath']).read_bytes()
        self.assertEqual(worker.sha256(raw).hexdigest(), receipt['evidenceSha256'])
        self.assertEqual(receipt['items'], 2)
        self.assertNotIn('publishDate', json.loads(raw)['result']['items'][0])
        self.no_admission()

    def test_source_failure_never_creates_evidence_or_admission(self):
        with patch.object(CAPABILITY_SOURCES, 'query', side_effect=ValueError('source-failure')) as consume:
            with self.assertRaises(ValueError):
                worker.collect(payload('collect'), self.database, self.directory)
        self.assertEqual(consume.call_count, 1)
        self.assertFalse(self.directory.exists())
        self.no_admission()

    def test_stale_evidence_cannot_be_applied(self):
        request, value = self.evidence(datetime.now(timezone.utc) - timedelta(minutes=31))
        with self.assertRaisesRegex(ValueError, '30分钟'):
            worker.apply_or_verify(self.save(request, value), self.database, self.directory)
        self.no_admission()

    def test_legacy_result_contract_remains_supported_without_writing(self):
        request, value = self.evidence()
        value['result']['metadataVersion'] = 'research-report-metadata-v1'
        _, result = worker.validate_evidence(value, request, self.state,
            datetime.now(timezone.utc), fresh=True)
        self.assertEqual(result.metadataVersion, 'research-report-metadata-v1')
        self.no_admission()

    def test_malformed_result_is_rejected_as_validation_error(self):
        for malformed in (None, [], 'invalid-result'):
            request, value = self.evidence()
            value['result'] = malformed
            with self.assertRaises(ValueError):
                worker.validate_evidence(value, request, self.state,
                    datetime.now(timezone.utc), fresh=True)
            self.no_admission()

    def test_scope_or_page_or_revision_mismatch_is_rejected(self):
        changes = [('symbol', '000001.SZ'), ('page', 2), ('sourceRevision', 'changed')]
        for name, value in changes:
            with self.subTest(name=name):
                request, evidence = self.evidence()
                if name == 'symbol': request[name] = value
                elif name == 'page': evidence['request'][name] = value
                else: evidence['result'][name] = value
                with self.assertRaises(ValueError):
                    worker.apply_or_verify(self.save(request, evidence), self.database, self.directory)
                self.no_admission()

    def test_content_tampering_is_rejected_before_recording(self):
        request, value = self.evidence()
        request = self.save(request, value)
        (self.directory / (request['digest'] + '.json')).write_text('{}')
        with self.assertRaisesRegex(ValueError, '摘要'):
            worker.apply_or_verify(request, self.database, self.directory)
        self.no_admission()

    def test_control_change_at_write_transaction_rolls_back(self):
        request, value = self.evidence()
        request = self.save(request, value)
        changed = {**self.state, 'policyRevision': 999}
        with patch.object(worker, 'control_state', side_effect=[self.state, changed]):
            with self.assertRaisesRegex(ValueError, '控制状态变化'):
                worker.apply_or_verify(request, self.database, self.directory)
        self.no_admission()

    def test_no_target_schema_is_initialized(self):
        missing = Path(self.temp.name) / 'missing.db'
        with self.assertRaises(worker.sqlite3.OperationalError):
            worker.control_store(missing).get_route_admission_v3(key=worker.KEY, target=worker.TARGET)
        self.assertFalse(missing.exists())

    def test_symlink_evidence_directory_is_rejected(self):
        other = Path(self.temp.name) / 'other'
        other.mkdir()
        self.directory.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '软链接'):
            worker.evidence_path(self.directory, 'a' * 64)


if __name__ == '__main__':
    unittest.main()
