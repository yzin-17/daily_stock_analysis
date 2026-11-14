"""用隔离 SQLite 验证招商银行准入的替换、续验和并发保护，无外部调用。"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import hithink_arena_news_admission as news


class Admission(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "control.sqlite"
        self.directory = self.db.parent / "thesis-ledger-route-evidence"
        self.directory.mkdir()
        self.at = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
        old = {"review": "旧准入"}
        digest = news.record_sha256(old)
        (self.directory / (digest + ".json")).write_bytes(news.canonical_bytes(old))
        self.previous = dict(
            consumer="thesis-ledger", provider_id="hithink", upstream_source=news.TARGET["upstreamSource"],
            route_key_json=json.dumps(news.KEY), status="admitted", invalidated_at=None, invalidation_reason=None,
            scope_symbols_json='["600519.SH"]', scope_date_from="2026-10-01", scope_date_to="2026-10-31",
            valid_from="2026-10-31T00:00:00+00:00", valid_until="2026-10-31T01:00:00+00:00",
            adapter_revision="a", source_revision="s", credential_revision="c", record_version=5,
            evidence_ref="sha256:" + digest, evidence_sha256=digest, recorded_by="prior", recorded_at="2026-10-31")
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TABLE thesis_ledger_route_admission_v3 (" + ",".join(
                k + (" INTEGER" if k == "record_version" else " TEXT") for k in self.previous) + ")")
            for row in [self.previous, {**self.previous, "provider_id":"akshare"}]:
                c.execute("INSERT INTO thesis_ledger_route_admission_v3 VALUES (" +
                          ",".join("?" for _ in row) + ")", tuple(row.values()))

    def evidence(self, previous, at):
        return {"symbol":news.SYMBOL, "target":news.TARGET, "previousAdmission":previous,
                "previousRecordSha256":news.record_sha256(previous), "observedAt":at.isoformat(),
                "validUntil":(at + news.TERM).isoformat(), "scopeDateFrom":(at - timedelta(days=3)).date().isoformat(),
                "scopeDateTo":(at + news.TERM).date().isoformat()}

    def test_preserves_old_evidence_and_other_routes_across_renewal(self):
        current = news.apply_record(self.db, self.previous, self.evidence(self.previous, self.at), lambda: None)
        self.assertEqual(current["record_version"], 6)
        self.assertEqual(json.loads(current["scope_symbols_json"]), ["600036.SH"])
        next_row = news.apply_record(self.db, current, self.evidence(current, self.at + news.TERM), lambda: None)
        self.assertEqual(news.verify_record(self.directory, next_row)["previousAdmission"], current)
        self.assertEqual(news.verify_record(self.directory, current)["previousAdmission"], self.previous)
        with closing(sqlite3.connect(self.db)) as c:
            c.row_factory = sqlite3.Row
            self.assertEqual(dict(c.execute("SELECT * FROM thesis_ledger_route_admission_v3 WHERE provider_id='akshare'").fetchone()),
                             {**self.previous, "provider_id":"akshare"})

    def test_conflict_or_revoked_or_active_other_scope_never_writes(self):
        evidence = self.evidence(self.previous, self.at)
        before = self.db.read_bytes()
        with self.assertRaises(AssertionError):
            news.apply_record(self.db, {**self.previous, "record_version": 4}, evidence, lambda: None)
        def revoked():
            raise RuntimeError("来源状态已变")
        with self.assertRaises(RuntimeError):
            news.apply_record(self.db, self.previous, evidence, revoked)
        self.assertEqual(before, self.db.read_bytes())
        for patch in [{"status":"revoked"}, {"scope_symbols_json":'["600000.SH"]'},
                      {"valid_until":(self.at + timedelta(hours=1)).isoformat()}]:
            with self.assertRaises(AssertionError):
                news.check_scope({**self.previous, **patch}, self.at)


if __name__ == "__main__":
    unittest.main()
