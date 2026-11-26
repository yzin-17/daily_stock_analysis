"""恢复应用 SQLite 副本并以只读连接核对完整性。"""
from pathlib import Path
import shutil
import sqlite3
import sys


def restore_sqlite(source, directory):
    destination = Path(directory) / "stock_analysis.db"
    if destination.exists() or destination.is_symlink():
        raise ValueError("恢复目标数据库已存在")
    shutil.copyfile(source, destination)
    # 在线备份包含已提交页面；校验独占副本时 SQLite 无需创建 WAL 辅助文件。
    connection = sqlite3.connect(destination.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        if connection.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite 副本完整性检查失败")
    finally:
        connection.close()


if __name__ == "__main__":
    restore_sqlite(*sys.argv[1:3])
