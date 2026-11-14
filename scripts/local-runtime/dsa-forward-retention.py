"""前向恢复独立核对：SQLite 比较完整逻辑内容，其余对象逐字节保持。"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile


AUXILIARY = 'rule-source-monitor.sqlite'


def sqlite_fingerprint(path):
    with closing(sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True)) as connection:
        connection.execute('BEGIN')
        schema = connection.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        tables = {}
        for kind, name, _table, _sql in schema:
            if kind == 'table':
                rows = connection.execute('SELECT * FROM "' + name.replace('"', '""') + '"').fetchall()
                hashes = sorted(hashlib.sha256(repr(row).encode()).hexdigest() for row in rows)
                tables[name] = {'rows': len(rows), 'sha256': hashlib.sha256(json.dumps(hashes).encode()).hexdigest()}
        return {'schemaSha256': hashlib.sha256(json.dumps(schema).encode()).hexdigest(), 'tables': tables}


def archive_fingerprint(archive):
    with tarfile.open(archive) as source, tempfile.NamedTemporaryFile() as temporary:
        members = [member for member in source.getmembers() if member.name.removeprefix('./') == AUXILIARY]
        assert len(members) == 1 and members[0].isfile(), '规则监测器备份身份不符'
        temporary.write(source.extractfile(members[0]).read())
        temporary.flush()
        return sqlite_fingerprint(temporary.name)


def verify(payload):
    module = {'__name__': 'forward_retention_inventory'}
    exec(payload['inventoryCode'], module)
    baseline = payload['baseline']
    path = Path('/app/data/stock_analysis.db')
    assert module['os'].environ['DATABASE_PATH'] == str(path)
    actual_auxiliary = sqlite_fingerprint(path.parent / AUXILIARY)
    assert actual_auxiliary == payload['auxiliary'], '规则监测器表结构或原记录变化'
    with closing(module['readonly_database'](path)) as connection:
        tables = module['compare_tables'](baseline['tables'], module['database_inventory'](connection, baseline['tables']))
    current_files = module['file_inventory'](path.parent, path)
    old_files = dict(baseline['files'])
    previous, current = old_files.pop(AUXILIARY), current_files.pop(AUXILIARY)
    objects = module['compare_objects'](old_files, current_files)
    assert not sum(row['addedRows'] for row in tables) and not any(row['added'] for row in objects), '存在未登记增量'
    return {'passed': True, 'readOnly': True, 'primaryTables': len(tables), 'otherFiles': len(objects),
            'auxiliary': actual_auxiliary, 'auxiliaryPhysicalBytesChanged': previous != current,
            'previousPhysical': previous, 'currentPhysical': current}


if __name__ == '__main__':
    try:
        value = archive_fingerprint(sys.argv[2]) if sys.argv[1] == 'baseline' else verify(json.load(sys.stdin))
        print(json.dumps(value, sort_keys=True))
    except Exception as error:
        print(json.dumps({'passed': False, 'errorType': type(error).__name__,
                          'reason': str(error) if isinstance(error, AssertionError) else '只读留存核对失败'}))
        sys.exit(1)
