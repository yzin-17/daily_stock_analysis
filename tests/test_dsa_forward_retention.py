"""独立 SQLite 留存比较不能把真实记录或结构变化当作存储字节变化。"""
import importlib.util
from pathlib import Path
import sqlite3


def test_sqlite_fingerprint_ignores_storage_header_but_detects_rows_and_schema(tmp_path):
    path = Path(__file__).parents[1] / 'scripts/local-runtime/dsa-forward-retention.py'
    spec = importlib.util.spec_from_file_location('dsa_forward_retention', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    database = tmp_path / 'monitor.sqlite'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE sample (id TEXT PRIMARY KEY, value TEXT)')
        connection.execute("INSERT INTO sample VALUES ('a','original')")
    first = module.sqlite_fingerprint(database)
    with sqlite3.connect(database) as connection:
        connection.execute('VACUUM')
    assert module.sqlite_fingerprint(database) == first
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE sample SET value='changed'")
    second = module.sqlite_fingerprint(database)
    assert second != first and second['schemaSha256'] == first['schemaSha256']
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE INDEX sample_value ON sample(value)')
    assert module.sqlite_fingerprint(database)['schemaSha256'] != second['schemaSha256']
