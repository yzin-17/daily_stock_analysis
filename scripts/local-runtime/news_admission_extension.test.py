"""用独立 SQLite 文件验证准入范围更新、证据链和并发拒绝，不请求真实来源。"""
from datetime import datetime, timezone
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import news_admission_extension as extension


NOW = datetime(2026, 10, 23, 11, tzinfo=timezone.utc)
KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'NEWS_SEARCH'}
TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}
REVISIONS = ('akshare-eastmoney-news-v1', 'reviewed-source-v1')
EVIDENCE = {'scopeDateFrom': '2026-10-16', 'scopeDateTo': '2026-10-28', 'items': [{'title': '公开标题'}]}


class NewsAdmissionExtension(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.db = Path(self.temporary.name) / 'controlled.db'
        self.directory = self.db.parent / 'thesis-ledger-route-evidence'
        self.directory.mkdir()
        previous_proof = b'{"items":[{"title":"previous public metadata"}]}'
        digest = sha256(previous_proof).hexdigest()
        self.old_path = self.directory / (digest + '.json')
        self.old_path.write_bytes(previous_proof)
        self.previous = {
            'consumer': 'thesis-ledger', 'route_key_json': json.dumps(KEY, sort_keys=True),
            'provider_id': 'akshare', 'upstream_source': 'eastmoney', 'status': 'admitted',
            'evidence_ref': 'sha256:' + digest, 'evidence_sha256': digest,
            'scope_symbols_json': '["600519.SH"]', 'scope_date_from': '2026-10-18',
            'scope_date_to': '2026-10-28', 'adapter_revision': REVISIONS[0], 'source_revision': REVISIONS[1],
            'credential_revision': 'not-required', 'valid_from': '2026-10-21T20:45:55+00:00',
            'valid_until': '2026-10-28T20:45:55+00:00', 'recorded_by': 'prior-operation',
            'record_version': 1, 'recorded_at': '2026-10-21T20:45:55+00:00',
            'invalidated_at': None, 'invalidation_reason': None,
        }
        with closing(sqlite3.connect(self.db)) as connection, connection:
            columns = ','.join(name + (' INTEGER' if name == 'record_version' else ' TEXT') for name in self.previous)
            connection.execute('CREATE TABLE thesis_ledger_route_admission_v3 (' + columns +
                               ', PRIMARY KEY (consumer,route_key_json,provider_id,upstream_source))')
            connection.execute('INSERT INTO thesis_ledger_route_admission_v3 VALUES (' +
                               ','.join('?' for _ in self.previous) + ')', tuple(self.previous.values()))
            connection.execute('CREATE TABLE protected_sentinel (value TEXT)')
            connection.execute("INSERT INTO protected_sentinel VALUES ('unchanged')")
        self.payload = {'mode': 'apply', 'providerId': 'akshare', 'symbol': '600519.SH',
                        'start': '2026-10-16', 'end': '2026-10-28', 'validHours': None,
                        'recordedBy': 'g3-reviewed-extension', 'extendExisting': True,
                        'expectedRecordVersion': 1, 'expectedRecordSha256': extension.record_sha256(self.previous)}

    def read(self):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.row_factory = sqlite3.Row
            self.assertEqual(connection.execute('SELECT value FROM protected_sentinel').fetchone()[0], 'unchanged')
            return extension.read_record(connection, KEY, TARGET)

    def apply(self, payload=None, guard=lambda: True, revisions=REVISIONS, credential='not-required'):
        return extension.extend_existing_news_admission(self.db, payload or self.payload, EVIDENCE,
                                                        KEY, TARGET, revisions, credential, guard, NOW)

    def replace(self, **fields):
        with closing(sqlite3.connect(self.db)) as connection, connection:
            connection.execute('UPDATE thesis_ledger_route_admission_v3 SET ' +
                               ','.join(name + '=?' for name in fields), tuple(fields.values()))
        self.payload['expectedRecordSha256'] = extension.record_sha256(self.read())

    def test_success_updates_only_approved_fields_and_readonly_verify_preserves_bytes(self):
        old_proof = self.old_path.read_bytes()
        result = self.apply()
        current = self.read()
        self.assertEqual(result['recordVersion'], 2)
        self.assertEqual(result['validUntil'], self.previous['valid_until'])
        self.assertEqual(result['scopeDateTo'], self.previous['scope_date_to'])
        self.assertEqual(self.old_path.read_bytes(), old_proof)
        self.assertEqual({key for key in current if current[key] != self.previous[key]}, extension.CHANGED_COLUMNS)
        saved = json.loads(Path(result['evidencePath']).read_bytes())
        self.assertEqual(saved['scopeExtension']['previousAdmission'], self.previous)
        self.assertEqual(saved['scopeExtension']['previousAdmissionSha256'], self.payload['expectedRecordSha256'])
        before_db = self.db.read_bytes()
        before_files = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        verified = self.apply({**self.payload, 'mode': 'verify'})
        self.assertFalse(verified['writes'])
        self.assertEqual(verified['evidenceSha256'], result['evidenceSha256'])
        self.assertEqual(self.db.read_bytes(), before_db)
        self.assertEqual({p.name: p.read_bytes() for p in self.directory.iterdir()}, before_files)

    def test_version_and_full_record_drift_fail_before_writing_evidence(self):
        for fields in [{'record_version': 2}, {'recorded_by': 'concurrent-operation'}]:
            with self.subTest(fields=fields):
                with closing(sqlite3.connect(self.db)) as connection, connection:
                    connection.execute('UPDATE thesis_ledger_route_admission_v3 SET ' +
                                       ','.join(key + '=?' for key in fields), tuple(fields.values()))
                before = self.read()
                with self.assertRaises(AssertionError):
                    self.apply()
                self.assertEqual(self.read(), before)
                self.assertEqual(len(list(self.directory.iterdir())), 1)
                with closing(sqlite3.connect(self.db)) as connection, connection:
                    connection.execute('UPDATE thesis_ledger_route_admission_v3 SET record_version=1,recorded_by=?',
                                       (self.previous['recorded_by'],))

    def test_credentials_and_source_enablement_are_checked_while_write_lock_is_held(self):
        def changed_guard():
            with closing(sqlite3.connect(self.db, timeout=0)) as competitor, competitor:
                with self.assertRaises(sqlite3.OperationalError):
                    competitor.execute('BEGIN IMMEDIATE')
            return False
        with self.assertRaisesRegex(AssertionError, '启停或凭据'):
            self.apply(guard=changed_guard)
        self.assertEqual(self.read(), self.previous)
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_revoked_expired_scope_or_revision_changes_are_rejected(self):
        cases = [
            {'status': 'rejected'}, {'invalidated_at': NOW.isoformat()}, {'invalidation_reason': 'revoked'},
            {'valid_until': NOW.isoformat()}, {'valid_from': '2026-10-24T00:00:00+00:00'},
            {'scope_symbols_json': '["000001.SZ"]'}, {'adapter_revision': 'changed'},
            {'source_revision': 'changed'}, {'credential_revision': 'changed'},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                self.replace(**fields)
                before = self.read()
                with self.assertRaises(AssertionError):
                    self.apply()
                self.assertEqual(self.read(), before)
                self.replace(**{key: self.previous[key] for key in fields})
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_new_expiry_wider_end_foreign_source_and_outside_seven_days_are_rejected(self):
        for fields in [{'validHours': 168}, {'end': '2026-10-29'}, {'start': '2026-10-15'},
                       {'start': '2026-10-18'}, {'providerId': 'yfinance'}, {'symbol': '000001.SZ'}]:
            with self.subTest(fields=fields), self.assertRaises(AssertionError):
                self.apply({**self.payload, **fields})
        self.assertEqual(self.read(), self.previous)
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_previous_evidence_missing_changed_or_symlink_is_rejected(self):
        previous = self.old_path.read_bytes()
        self.old_path.write_bytes(b'changed')
        with self.assertRaises(AssertionError):
            self.apply()
        self.old_path.unlink()
        with self.assertRaises(AssertionError):
            self.apply()
        actual = self.directory / 'foreign.json'; actual.write_bytes(previous)
        self.old_path.symlink_to(actual)
        with self.assertRaises(AssertionError):
            self.apply()
        self.assertEqual(self.read(), self.previous)


if __name__ == '__main__':
    unittest.main()
