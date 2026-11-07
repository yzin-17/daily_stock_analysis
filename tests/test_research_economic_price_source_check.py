"""只读资格采集的边界、错误停止和经济资格不提升。"""

from datetime import datetime
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from zoneinfo import ZoneInfo


path = Path(__file__).parents[1] / 'scripts/research-economic-price-source-check.py'
spec = importlib.util.spec_from_file_location('economic_source_check', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EconomicSourceChecks(unittest.TestCase):
    def test_plan_is_two_single_symbol_bounded_reads_without_adjusted_price(self):
        reads = module.plan('600519.SH', '2026-10-29', '2026-11-05')
        self.assertEqual(len(reads), 2)
        self.assertEqual(reads[1][2]['adjust'], 'none')
        for row in reads:
            self.assertEqual(row[2]['thscode'], '600519.SH')

    def test_bad_symbol_or_unbounded_window_rejected_before_io(self):
        for symbol, start, end in [('600519', '2026-10-29', '2026-11-05'),
                                   ('600519.SH', '2026-01-01', '2026-11-05'),
                                   ('600519.SH', '2026-11-05', '2026-10-29')]:
            with self.subTest(symbol=symbol, start=start, end=end):
                with self.assertRaises(ValueError):
                    module.plan(symbol, start, end)

    def test_explicit_empty_events_never_grant_economic_qualification(self):
        value = module.validate('corporate_actions',
            {'thscode': '600519.SH', 'ticker': '600519', 'item': []},
            '600519.SH', '2026-10-29', '2026-11-05')
        self.assertTrue(value['eventFreeCandidate'])
        self.assertEqual(value['economicQualification'], 'not_verified')

    def test_wrong_security_and_missing_event_array_rejected(self):
        for value in [{'thscode': '000001.SZ', 'ticker': '600519', 'item': []},
                      {'thscode': '600519.SH', 'ticker': '600519'}]:
            with self.assertRaises(ValueError):
                module.validate('corporate_actions', value,
                                '600519.SH', '2026-10-29', '2026-11-05')

    def test_numeric_unknown_or_nonfinite_is_rejected(self):
        for value in [None, True, '0', float('nan'), float('inf'), -1]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    module.number(value)

    def test_millisecond_date_requires_shanghai_midnight(self):
        stamp = int(datetime(2026, 10, 29, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)
        self.assertEqual(module.market_day(stamp), '2026-10-29')
        with self.assertRaises(ValueError):
            module.market_day(stamp + 1000)

    def test_http_business_or_protocol_failure_stops_without_retry(self):
        for status, wire in [(403, {}), (200, {'code': 2001}),
                             (200, {'code': 0, 'data': {'item': []}})]:
            calls = []

            def fetch(url, **kwargs):
                calls.append((url, kwargs))
                return SimpleNamespace(status_code=status, content=json.dumps(wire).encode())

            value = module.collect('600519.SH', '2026-10-29', '2026-11-05',
                                   'secret-not-for-output', fetch)
            self.assertFalse(value['transportAndProtocolPassed'])
            self.assertEqual(len(calls), 1)
            self.assertEqual(value['sourceRequests'], 1)
            self.assertFalse(calls[0][1]['allow_redirects'])
            self.assertNotIn('secret-not-for-output', json.dumps(value))

    def test_empty_prices_are_not_a_success(self):
        with self.assertRaises(ValueError):
            module.validate('raw_prices', {'item': []},
                            '600519.SH', '2026-10-29', '2026-11-05')

    def test_all_history_keeps_original_records_and_only_counts_fixed_window(self):
        stamp = int(datetime(2026, 6, 1, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)
        data = {'thscode': '600519.SH', 'ticker': '600519', 'item': [
            {'ticker': '600519', 'ex_date_ms': stamp, 'dividend_per_share': 1,
             'per_share_bonus': 0}]}
        value = module.validate('corporate_actions', data, '600519.SH',
                                '2026-10-29', '2026-11-05', event_history=True)
        self.assertEqual(value['rowCount'], 1)
        self.assertEqual(value['windowEventCount'], 0)
        self.assertEqual(value['economicQualification'], 'not_verified')
        self.assertNotIn('from', module.plan('600519.SH', '2026-10-29',
                                            '2026-11-05', True)[0][2])


if __name__ == '__main__':
    unittest.main()
