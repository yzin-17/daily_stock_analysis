"""在线备份保持原库、WAL 可见事实和原始数据字节。"""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("snapshot_state", Path(__file__).with_name("snapshot-state.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SnapshotTests(unittest.TestCase):
    def test_online_wal_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state with space & ?.db"
            source = sqlite3.connect(path)
            source.execute("pragma journal_mode=wal")
            source.execute("create table sample(value text)")
            source.execute("insert into sample values (?)", ("受控原始值",))
            source.commit()
            before = path.read_bytes()
            snapshot = module.snapshot(path)
            restored = Path(directory) / "restored.db"
            restored.write_bytes(snapshot)
            target = sqlite3.connect(restored)
            self.assertEqual(target.execute("select value from sample").fetchall(), [("受控原始值",)])
            self.assertEqual(path.read_bytes(), before)
            target.close()
            source.close()

    def test_missing_source_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            with self.assertRaises(sqlite3.OperationalError):
                module.snapshot(path)
            self.assertFalse(path.exists())


unittest.main()
