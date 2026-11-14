"""市场资料增量路由的只读计划；写入由 Server 现有策略 API 承担。"""

from hashlib import sha256
import json
from pathlib import Path
import sqlite3


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def protected(connection):
    names = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
             if r[0].startswith('thesis_ledger_') and ('provider_config' in r[0] or 'admission' in r[0])]
    return {name: digest(sorted(digest(dict(r)) for r in connection.execute('SELECT * FROM "' + name + '"')))
            for name in sorted(names)}


def policy_row(connection):
    row = connection.execute('SELECT * FROM thesis_ledger_policy_state WHERE consumer=?', ('thesis-ledger',)).fetchone()
    if row is None:
        raise ValueError('必须存在原有效策略，不创建或开启新策略')
    return dict(row)


def store_for(database):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore
    class Store(ThesisLedgerControlStore):
        def __init__(self):
            self.database_path = str(Path(database).resolve())
        def _connect(self):
            connection = sqlite3.connect(Path(self.database_path).as_uri() + '?mode=ro',
                uri=True, isolation_level=None, timeout=10)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            return connection
    return Store()


def additions(store, previous):
    from data_provider.market_reference_bindings import READERS
    from src.services.thesis_ledger_reference_catalog_v1 import capability
    from src.services.thesis_ledger_reference_scope_v1 import asset_type
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    from src.services.thesis_ledger_price_route_access import basic_price_route_configured
    providers = {r['providerId']: r for r in store.provider_registry()}
    result = []
    for dataset, (provider, source, _, _) in READERS.items():
        row = providers[provider]
        if not row['enabled'] or not row['configured']:
            raise ValueError('现有来源必须已配置启用')
        key = {'kind': 'data', 'market': 'CN', 'assetType': asset_type(dataset),
               'capability': capability(dataset)}
        target = {'providerId': provider, 'upstreamSource': source}
        binding = CAPABILITY_SOURCES.resolve(key, target)
        if binding is None or binding.admission_required is not False or not basic_price_route_configured(store, key, target):
            raise ValueError('精确绑定或原来源依赖不满足')
        route = {'key': key, 'targets': [target], 'enabled': True}
        existing = next((r for r in previous if r['key'] == key), None)
        if existing is not None and existing != route:
            raise ValueError('既有同名路由不同；拒绝覆盖')
        if existing is None:
            result.append(route)
    return result


def main(payload):
    from src.services.thesis_ledger_control import _database_path
    if payload != {'mode': 'plan', 'expected': None}:
        raise ValueError('路由执行输入无效')
    store = store_for(_database_path())
    with store._connect() as connection:
        before, protection = policy_row(connection), protected(connection)
    if not before['enabled'] or before['status'] != 'applied':
        raise ValueError('现有策略必须已经启用且成功应用')
    old_routes = json.loads(before['routes_json'])
    extra = additions(store, old_routes)
    retired_names = {'REFERENCE_INDUSTRY_RANKING', 'REFERENCE_REPORT_EPS'}
    retirements = [r for r in old_routes if r['key'].get('capability') in retired_names]
    for route in retirements:
        key = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': route['key']['capability']}
        if route != {'key': key, 'targets': [{'providerId': 'akshare', 'upstreamSource': 'eastmoney'}], 'enabled': True}:
            raise ValueError('待撤销的本轮路由已被修改，拒绝清理')
    plan = {'format': 'market-reference-routes-v1', 'policySha256': digest(before), 'protected': protection,
            'previousRevision': before['revision'], 'previousRoutesSha256': digest(old_routes),
            'previousRoutes': old_routes, 'additions': extra, 'retirements': retirements}
    return plan


if __name__ == '__main__':
    import sys
    print(json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
