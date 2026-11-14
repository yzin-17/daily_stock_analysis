"""留存验收的腐损、增量和只读边界回归。"""

from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("retention", Path(__file__).with_name("dsa-state-retention.py"))
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "state.sqlite"
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE legacy(id INTEGER PRIMARY KEY, secret TEXT, blob BLOB)")
            conn.execute("INSERT INTO legacy VALUES(1, 'do-not-export-plaintext', X'0001')")
        with closing(retention.readonly_database(self.path)) as conn:
            self.baseline = retention.database_inventory(conn)

    def current(self):
        with closing(retention.readonly_database(self.path)) as conn:
            return retention.database_inventory(conn, self.baseline)

    def test_logical_inventory_ignores_layout_and_does_not_export_values(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("VACUUM")
        observed = retention.compare_tables(self.baseline, self.current())
        self.assertEqual(observed[0]["originalRows"], 1)
        self.assertNotIn("do-not-export", json.dumps(self.baseline))
        self.assertNotIn("do-not-export", json.dumps(observed))

    def test_original_row_change_is_rejected(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE legacy SET secret='changed'")
        with self.assertRaisesRegex(AssertionError, "原记录缺失或变化"):
            retention.compare_tables(self.baseline, self.current())

    def test_original_row_deletion_is_rejected(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM legacy")
        with self.assertRaises(AssertionError):
            retention.compare_tables(self.baseline, self.current())

    def test_additive_column_retains_original_column_comparison(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("ALTER TABLE legacy ADD COLUMN new_state TEXT")
            conn.execute("UPDATE legacy SET new_state='new-only'")
        self.assertEqual(retention.compare_tables(self.baseline, self.current())[0]["addedRows"], 0)

    def test_new_rows_are_counted_separately(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO legacy VALUES(2, 'new', NULL)")
        row = retention.compare_tables(self.baseline, self.current())[0]
        self.assertEqual((row["originalRows"], row["currentRows"], row["addedRows"]), (1, 2, 1))

    def test_missing_table_or_changed_column_is_rejected(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("ALTER TABLE legacy RENAME TO moved")
        with self.assertRaisesRegex(AssertionError, "原表缺失"):
            self.current()

    def test_readonly_snapshot_cannot_write_back(self):
        with closing(retention.readonly_database(self.path)) as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("DELETE FROM legacy")
        self.assertEqual(len(self.current()["legacy"]["rows"]), 1)

    def test_file_loss_and_corruption_are_not_hidden_by_equal_counts(self):
        old = {"a": {"bytes": 1, "sha256": "one"}, "b": {"bytes": 1, "sha256": "two"}}
        for value in [{"b": old["b"], "c": old["a"]}, {"a": old["a"], "b": old["a"]}]:
            with self.subTest(value=value), self.assertRaises(AssertionError):
                retention.compare_objects(old, value)

    def test_new_file_is_identified_by_digest_without_path_in_report(self):
        value = retention.compare_objects({}, {"private-path": {"bytes": 1, "sha256": "one"}})
        self.assertTrue(value[0]["added"])
        self.assertNotIn("private-path", json.dumps(value))

    def test_archive_traversal_and_links_are_rejected(self):
        for name, kind in [("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
                           ("linked", tarfile.SYMTYPE)]:
            with self.subTest(name=name):
                path = self.root / "input.tar"
                with tarfile.open(path, "w") as archive:
                    entry = tarfile.TarInfo(name)
                    entry.type = kind
                    entry.size = 1 if kind == tarfile.REGTYPE else 0
                    archive.addfile(entry, io.BytesIO(b"x"))
                with self.assertRaises(AssertionError):
                    retention.archive_inventory(path)

    def test_live_file_inventory_rejects_symbolic_links(self):
        (self.root / "link").symlink_to(self.path)
        with self.assertRaises(AssertionError):
            retention.file_inventory(self.root)


if __name__ == "__main__":
    unittest.main()
