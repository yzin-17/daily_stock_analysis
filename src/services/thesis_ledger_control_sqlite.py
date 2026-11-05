"""控制存储的连接与事务边界；退出时显式释放 SQLite 资源。"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3


@contextmanager
def control_connection(database_path: str):
    if database_path != ":memory:":
        Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA foreign_keys = ON")
        if database_path != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        with connection:
            yield connection
    finally:
        connection.close()
