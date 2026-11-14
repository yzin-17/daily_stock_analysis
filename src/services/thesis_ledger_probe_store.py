"""诊断专用控制读取；SQLite 查询连接不运行初始化和升级。"""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .thesis_ledger_control import ThesisLedgerControlStore, _database_path


class ReadOnlyProbeStore(ThesisLedgerControlStore):
    def __init__(self, database_path=None):
        self.database_path = database_path or _database_path()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(Path(self.database_path).resolve().as_uri() + '?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA query_only = ON')
            yield connection
        finally:
            connection.close()
