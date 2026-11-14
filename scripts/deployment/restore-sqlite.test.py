"""恢复只写入隔离目标，拒绝覆盖并保持源副本不变。"""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("restore_sqlite", Path(__file__).with_name("restore-sqlite.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RestoreTests(unittest.TestCase):
    def test_valid_copy_and_existing_target(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "copy.sqlite"
            connection = sqlite3.connect(source)
            connection.execute("create table sample(value text)")
            connection.execute("insert into sample values ('原始记录')")
            connection.commit()
            connection.close()
            original = source.read_bytes()
            target = Path(root) / "target #副本"
            target.mkdir()
            module.restore_sqlite(source, target)
            self.assertEqual((target / "stock_analysis.db").read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)
            with self.assertRaises(ValueError):
                module.restore_sqlite(source, target)

    def test_invalid_copy_fails(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "invalid.sqlite"
            source.write_bytes(b"invalid")
            with self.assertRaises(sqlite3.DatabaseError):
                module.restore_sqlite(source, root)


if __name__ == "__main__":
    unittest.main()
