"""缓存清理必须保留源码、其他文件与符号链接指向的数据。"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

spec = importlib.util.spec_from_file_location("cache_cleanup", Path(__file__).with_name("prune-bytecode.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CacheBoundaryTests(unittest.TestCase):
    def test_preview_apply_and_symlink_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "src"
            cache = root / "__pycache__"
            cache.mkdir(parents=True)
            (cache / "module.pyc").write_bytes(b"cache")
            (cache / "notes.json").write_text("{}")
            (root / "module.py").write_text("pass")
            data = Path(directory) / "data"
            data.mkdir()
            (data / "records.pyc").write_bytes(b"data")
            (cache / "link.pyc").symlink_to(data / "records.pyc")
            (root / "linked").symlink_to(data, target_is_directory=True)
            self.assertEqual(module.prune_bytecode([root]), {"apply": False, "files": 1, "bytes": 5})
            self.assertTrue((cache / "module.pyc").exists())
            self.assertEqual(module.prune_bytecode([root], True)["files"], 1)
            self.assertTrue((cache / "notes.json").exists())
            self.assertTrue((root / "module.py").exists())
            self.assertEqual((data / "records.pyc").read_bytes(), b"data")
            with self.assertRaises(ValueError):
                module.prune_bytecode([root / "linked"], True)


unittest.main()
