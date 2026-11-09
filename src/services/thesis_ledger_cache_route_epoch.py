"""由不可变已应用策略历史派生精确路由代次，保留关闭再开启的隔离。"""

from functools import lru_cache
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote


def _signature(enabled, routes, key):
    if not isinstance(routes, list):
        raise ValueError('invalid policy history')
    matching = [route for route in routes if isinstance(route, dict) and route.get('key') == key]
    if len(matching) > 1:
        raise ValueError('ambiguous route history')
    return bool(enabled), matching[0] if matching else None


@lru_cache(maxsize=512)
def _read_epoch(database_path, revision, encoded_key):
    key = json.loads(encoded_key)
    try:
        with sqlite3.connect('file:' + quote(database_path, safe='/') + '?mode=ro', uri=True) as connection:
            rows = connection.execute(
                'SELECT revision, enabled, routes_json FROM thesis_ledger_policy_history '
                'WHERE consumer=? AND status=? AND revision<=? ORDER BY revision DESC',
                ('thesis-ledger', 'applied', revision))
            current = next(rows, None)
            if current is None or current[0] != revision:
                return revision
            expected = _signature(current[1], json.loads(current[2]), key)
            epoch = revision
            for prior, enabled, routes_json in rows:
                if _signature(enabled, json.loads(routes_json), key) != expected:
                    break
                epoch = prior
            return epoch
    except (sqlite3.Error, ValueError, TypeError):
        # 历史缺失或不可读时退回全局代次，不能猜测旧数据仍有效。
        return revision


def route_cache_epoch(store, key, revision):
    database_path = getattr(store, 'database_path', None)
    if not isinstance(database_path, (str, Path)) or not Path(database_path).is_file():
        return revision
    return _read_epoch(str(Path(database_path).resolve()), revision,
                       json.dumps(key, sort_keys=True, separators=(',', ':')))
