"""隔离SQLite验证限时续验、原子冲突拒绝与证据链，禁止真实请求。"""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import news_admission_renewal as renewal

NOW = datetime(2026, 10, 29, 14, tzinfo=timezone.utc)
KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'NEWS_SEARCH'}
TARGET = {'providerId': 'hithink', 'upstreamSource': 'hithink-news-stock-timeline'}
REVISIONS = ('reviewed-adapter', 'reviewed-source')


class Renewal(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = Path(temporary.name) / 'control.db'
        self.directory = self.db.parent / 'thesis-ledger-route-evidence'
        self.directory.mkdir()
        raw = b'{"previous":"reviewed"}'
        digest = sha256(raw).hexdigest()
        (self.directory / (digest + '.json')).write_bytes(raw)
        self.previous = {
            'consumer': 'thesis-ledger', 'route_key_json': json.dumps(KEY, sort_keys=True),
            'provider_id': TARGET['providerId'], 'upstream_source': TARGET['upstreamSource'],
            'status': 'admitted', 'scope_symbols_json': '["600519.SH"]',
            'scope_date_from': '2026-10-16', 'scope_date_to': '2026-10-29',
            'adapter_revision': REVISIONS[0], 'source_revision': REVISIONS[1],
            'credential_revision': 'reviewed-credential', 'valid_from': '2026-10-22T06:00:00+00:00',
            'valid_until': '2026-10-29T06:00:00+00:00', 'evidence_ref': 'sha256:' + digest,
            'evidence_sha256': digest, 'record_version': 2, 'recorded_by': 'prior-review',
            'recorded_at': '2026-10-22T06:00:00+00:00', 'invalidated_at': None, 'invalidation_reason': None,
        }
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE thesis_ledger_route_admission_v3 (' + ','.join(
                k + (' INTEGER' if k == 'record_version' else ' TEXT') for k in self.previous) + ')')
            c.execute('INSERT INTO thesis_ledger_route_admission_v3 VALUES (' + ','.join('?' for _ in self.previous) + ')', tuple(self.previous.values()))
            c.execute('CREATE TABLE protected (value TEXT)')
            c.execute("INSERT INTO protected VALUES ('unchanged')")
        self.payload = {'mode': 'apply', 'providerId': 'hithink', 'symbol': '600519.SH',
                        'start': '2026-10-22', 'end': '2026-10-29', 'validHours': 1,
                        'observedAt': (NOW - timedelta(seconds=30)).isoformat(), 'recordedBy': 'reviewed-renewal',
                        'renewExisting': True, 'expectedRecordVersion': 2,
                        'expectedRecordSha256': renewal.record_sha256(self.previous)}
        self.evidence = {'items': [{'title': 'new public metadata'}]}

    def read(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            self.assertEqual(c.execute('SELECT value FROM protected').fetchone()[0], 'unchanged')
            return renewal.read_record(c, KEY, TARGET)

    def apply(self, payload=None, guard=lambda: True, credential='reviewed-credential', now=NOW):
        return renewal.renew_existing_news_admission(self.db, payload or self.payload, self.evidence,
                                                     KEY, TARGET, REVISIONS, credential, guard, now)

    def rolling_payload(self):
        self.previous['scope_date_from'] = '2026-10-22'
        with sqlite3.connect(self.db) as connection:
            connection.execute('UPDATE thesis_ledger_route_admission_v3 SET scope_date_from=?',
                               (self.previous['scope_date_from'],))
        now = NOW + timedelta(days=1)
        return {**self.payload, 'start': '2026-10-23', 'end': '2026-10-30',
                'observedAt': (now - timedelta(seconds=30)).isoformat(), 'rollRecentWindow': True,
                'expectedRecordSha256': renewal.record_sha256(self.previous)}, now

    def test_rolling_window_requires_explicit_choice_and_keeps_readonly_evidence_chain(self):
        payload, now = self.rolling_payload()
        implicit = {k: v for k, v in payload.items() if k != 'rollRecentWindow'}
        with self.assertRaises(AssertionError):
            self.apply(implicit, now=now)
        self.assertEqual(self.read(), self.previous)
        old_files = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        result = self.apply(payload, now=now)
        self.assertEqual(result['validUntil'], '2026-10-30T15:00:00+00:00')
        current = self.read()
        self.assertEqual((current['scope_date_from'], current['scope_date_to']),
                         ('2026-10-23', '2026-10-30'))
        evidence = json.loads(Path(result['evidencePath']).read_bytes())
        self.assertIs(evidence['scopeRenewal']['rollRecentWindow'], True)
        self.assertEqual(evidence['scopeRenewal']['previousAdmission'], self.previous)
        for name, raw in old_files.items():
            self.assertEqual((self.directory / name).read_bytes(), raw)
        before = self.db.read_bytes()
        self.assertFalse(self.apply({**payload, 'mode': 'verify'}, now=now)['writes'])
        self.assertEqual(self.db.read_bytes(), before)

    def test_rolling_window_rejects_false_choice_wider_and_multi_day_moves_without_writes(self):
        payload, now = self.rolling_payload()
        cases = [({**payload, 'rollRecentWindow': False}, now),
                 ({**payload, 'start': '2026-10-22'}, now),
                 ({**payload, 'start': '2026-10-24', 'end': '2026-10-31',
                   'observedAt': (now + timedelta(days=1)).isoformat()}, now + timedelta(days=1))]
        for value, at in cases:
            with self.subTest(value=value), self.assertRaises(AssertionError):
                self.apply(value, now=at)
            self.assertEqual(self.read(), self.previous)
            self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_atomic_success_retains_prior_evidence_and_readonly_verifies(self):
        old_files = {p.name: p.read_bytes() for p in self.directory.iterdir()}
        result = self.apply()
        self.assertEqual(result['validUntil'], '2026-10-29T15:00:00+00:00')
        self.assertEqual(result['recordVersion'], 3)
        for name, raw in old_files.items():
            self.assertEqual((self.directory / name).read_bytes(), raw)
        stored = json.loads(Path(result['evidencePath']).read_bytes())
        self.assertEqual(stored['scopeRenewal']['previousAdmission'], self.previous)
        raw = self.db.read_bytes()
        self.assertFalse(self.apply({**self.payload, 'mode': 'verify'})['writes'])
        self.assertEqual(self.db.read_bytes(), raw)
        with self.assertRaises(AssertionError):
            self.apply()
        self.assertEqual(self.read()['record_version'], 3)

    def test_scope_budget_freshness_version_digest_and_credentials_reject_without_writes(self):
        changes = [{'validHours': 2}, {'providerId': 'akshare'}, {'symbol': 'NVDA.US'},
                   {'start': '2026-10-21'}, {'end': '2026-10-30'}, {'expectedRecordVersion': 1},
                   {'expectedRecordSha256': 'f' * 64}, {'extendExisting': True},
                   {'observedAt': (NOW - timedelta(minutes=16)).isoformat()}]
        for fields in changes:
            with self.subTest(fields=fields), self.assertRaises(AssertionError):
                self.apply({**self.payload, **fields})
            self.assertEqual(self.read(), self.previous)
            self.assertEqual(len(list(self.directory.iterdir())), 1)
        with self.assertRaisesRegex(AssertionError, '凭据'):
            self.apply(credential='rotated')

    def test_active_revoked_and_moved_scope_are_not_silently_readmitted(self):
        for fields in [{'valid_until': '2026-10-30T00:00:00+00:00'}, {'status': 'revoked'},
                       {'invalidated_at': NOW.isoformat()}, {'scope_date_from': '2026-10-23'},
                       {'source_revision': 'changed'}]:
            with self.subTest(fields=fields):
                with sqlite3.connect(self.db) as c:
                    c.execute('UPDATE thesis_ledger_route_admission_v3 SET ' + ','.join(k + '=?' for k in self.previous), tuple({**self.previous, **fields}.values()))
                self.payload['expectedRecordSha256'] = renewal.record_sha256(self.read())
                with self.assertRaises(AssertionError):
                    self.apply()
                self.assertEqual(self.read(), {**self.previous, **fields})

    def test_enabled_and_credential_guard_runs_under_sqlite_write_lock(self):
        def guard():
            with sqlite3.connect(self.db, timeout=0) as competitor:
                with self.assertRaises(sqlite3.OperationalError):
                    competitor.execute('BEGIN IMMEDIATE')
            return False
        with self.assertRaisesRegex(AssertionError, '启停或凭据'):
            self.apply(guard=guard)
        self.assertEqual(self.read(), self.previous)
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_missing_prior_evidence_fails_before_creating_a_new_chain(self):
        next(self.directory.iterdir()).unlink()
        with self.assertRaises(AssertionError):
            self.apply()
        self.assertEqual(self.read(), self.previous)
        self.assertEqual(list(self.directory.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
