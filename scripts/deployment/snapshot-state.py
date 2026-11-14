"""只读在线复制应用 SQLite，并验证副本完整性。"""
import os
from pathlib import Path
import sqlite3
import sys


def snapshot(path):
    source = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(":memory:")
    try:
        source.backup(copy)
        if copy.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite 副本完整性检查失败")
        return copy.serialize()
    finally:
        source.close()
        copy.close()


if __name__ == "__main__":
    sys.stdout.buffer.write(snapshot(os.environ["DATABASE_PATH"]))
