"""只读增量计划与版本变化；使用隔离 SQLite，不连接 Docker 或外部来源。"""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reference_routes', Path(__file__).with_name('market-reference-routes.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RoutesTest(unittest.TestCase):
    def setUp(self):
        from src.services.thesis_ledger_control import ThesisLedgerControlStore
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = str(Path(self.directory.name) / 'control.db')
        self.store = ThesisLedgerControlStore(self.database)
        self.old = {'key': {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'REALTIME_QUOTE'},
                    'targets': [{'providerId': 'akshare', 'upstreamSource': 'sina'}]}
        self.store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'before',
                                   'revision': 1, 'enabled': True, 'routes': [self.old]})
        from data_provider.market_reference_bindings import READERS
        self.patchers = [patch('src.services.thesis_ledger_control._database_path', return_value=self.database),
                        patch('data_provider.market_reference_bindings.READERS', {'financial-balance': READERS['financial-balance']})]
        for patcher in self.patchers:
            patcher.start();self.addCleanup(patcher.stop)

    def test_plan_preserves_old_routes_and_database_is_readonly(self):
        plan = module.main({'mode': 'plan', 'expected': None})
        self.assertEqual(len(plan['additions']), 1)
        self.assertEqual(self.store.policy_projection_v3()['desired']['revision'], 1)
        self.assertEqual(self.store.policy_projection_v3()['desired']['routes'][0], self.old)
        import sqlite3
        with module.store_for(self.database)._connect() as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('DELETE FROM thesis_ledger_policy_state')
        with self.assertRaises(ValueError):
            module.main({'mode': 'apply', 'expected': plan})

    def test_stale_plan_never_overwrites_concurrent_policy(self):
        plan = module.main({'mode': 'plan', 'expected': None})
        self.store.apply_policy_v3({'contractVersion': 3, 'consumer': 'thesis-ledger', 'requestId': 'concurrent',
                                   'revision': 2, 'enabled': True, 'routes': [self.old]})
        after = module.main({'mode': 'plan', 'expected': None})
        self.assertNotEqual(plan['policySha256'], after['policySha256'])
        self.assertNotEqual(plan['previousRevision'], after['previousRevision'])
        self.assertEqual(self.store.policy_projection_v3()['desired']['requestId'], 'concurrent')

    def test_new_assets_use_contract_identity_without_replacing_old_route(self):
        from data_provider.market_reference_derivatives import DERIVATIVE_READERS
        names = ('futures-quote', 'etf-option-greeks', 'convertible-bond-reference')
        with patch('data_provider.market_reference_bindings.READERS', {name: DERIVATIVE_READERS[name] for name in names}):
            plan = module.main({'mode': 'plan', 'expected': None})
        self.assertEqual({route['key']['assetType'] for route in plan['additions']}, {'FUTURE', 'OPTION', 'CONVERTIBLE_BOND'})
        self.assertEqual(plan['previousRoutes'], [self.old])
        self.assertEqual(self.store.policy_projection_v3()['desired']['revision'], 1)


if __name__ == '__main__':
    unittest.main()
