"""指数日线选择与实际 fetcher 接缝的离线回归。"""

from datetime import datetime
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from data_provider.tushare_fetcher import TushareFetcher
from data_provider.tushare_index_daily_rows import select_latest_index_daily_row


CODES = ['000001.SH', '399001.SZ', '399006.SZ', '000688.SH', '000016.SH', '000300.SH']
NAMES = ['上证指数', '深证成指', '创业板指', '科创50', '上证50', '沪深300']


def row(code='000001.SH', date='20260928', close=11):
    return dict(ts_code=code, trade_date=date, close=close, pre_close=10, change=1,
                pct_chg=10, open=10.5, high=12, low=9, vol=123, amount=45)


class TestIndexDailyRows(unittest.TestCase):
    def setUp(self):
        for target in ['socket.socket', 'socket.create_connection', 'requests.sessions.Session.request']:
            blocker = patch(target, side_effect=AssertionError('离线测试禁止网络'))
            blocker.start()
            self.addCleanup(blocker.stop)
        self.fetcher = object.__new__(TushareFetcher)
        self.fetcher._api = Mock()
        self.fetcher._check_rate_limit = Mock()
        clock = patch('data_provider.tushare_fetcher.datetime', wraps=datetime)
        mocked_clock = clock.start()
        mocked_clock.now.return_value = datetime(2026, 9, 28, 12)
        self.addCleanup(clock.stop)

    def read(self, frame):
        self.fetcher._api.index_daily.side_effect = lambda **kwargs: (
            frame if kwargs['ts_code'] == CODES[0] else pd.DataFrame()
        )
        return self.fetcher.get_main_indices()

    def test_order_independent_original_row_and_unchanged_frame(self):
        records = [row(date='20260923', close=3), row(date='20260928', close=11),
                   row(date='20260925', close=5)]
        for order in [records, records[::-1], sorted(records, key=lambda r: r['trade_date'])]:
            with self.subTest(order=[r['trade_date'] for r in order]):
                frame = pd.DataFrame(order, index=[7, 7, 2])
                before = frame.copy(deep=True)
                selected = select_latest_index_daily_row(frame, CODES[0], '20260923', '20260928')
                self.assertEqual(selected.to_dict(), records[1])
                pd.testing.assert_frame_equal(frame, before)
                self.assertEqual(self.read(frame)[0]['current'], 11)

    def test_six_mappings_request_window_and_numeric_output(self):
        self.fetcher._api.index_daily.side_effect = lambda **kwargs: pd.DataFrame([row(kwargs['ts_code'])])
        results = self.fetcher.get_main_indices()
        self.assertEqual(len(results), 6)
        for result, code, name in zip(results, CODES, NAMES):
            self.assertEqual(result, dict(code=code.split('.')[0], name=name, current=11.0,
                                          change=1.0, change_pct=10.0, open=10.5, high=12.0,
                                          low=9.0, prev_close=10.0, volume=123.0,
                                          amount=45000.0, amplitude=0.0))
        self.assertEqual([call.kwargs for call in self.fetcher._api.index_daily.call_args_list],
                         [dict(ts_code=code, start_date='20260923', end_date='20260928') for code in CODES])

    def test_inclusive_window_boundaries(self):
        for date in ['20260923', '20260928']:
            with self.subTest(date=date):
                self.assertEqual(self.read(pd.DataFrame([row(date=date)]))[0]['current'], 11)

    def test_reject_invalid_date_anywhere_in_response(self):
        invalid = [None, pd.NA, float('nan'), 20260928, '', '2026-09-28', '2026092',
                   '202609280', ' 20260928', '２０２６０９２８', '20260230', '20261301',
                   '00000928', '20260922', '20260929']
        for date in invalid:
            with self.subTest(date=date):
                frame = pd.DataFrame([row(), row(date=date)])
                with self.assertRaises(ValueError):
                    select_latest_index_daily_row(frame, CODES[0], '20260923', '20260928')
                self.assertIsNone(self.read(frame))

    def test_reject_wrong_missing_or_null_identity(self):
        for code in ['000001.SZ', '000001', '000001.SH ', '', None, pd.NA, float('nan')]:
            with self.subTest(code=code):
                self.assertIsNone(self.read(pd.DataFrame([row(), row(code=code, date='20260925')])))

    def test_reject_duplicates_even_if_identical_or_older(self):
        for duplicate in [row(), row(close=20), row(date='20260925')]:
            records = [row(), duplicate]
            if duplicate['trade_date'] != '20260928':
                records.append(duplicate.copy())
            with self.subTest(records=records):
                self.assertIsNone(self.read(pd.DataFrame(records)))

    def test_missing_columns_and_duplicate_columns(self):
        for column in ['ts_code', 'trade_date']:
            with self.subTest(column=column):
                self.assertIsNone(self.read(pd.DataFrame([row()]).drop(columns=column)))
        frame = pd.DataFrame([row()])
        self.assertIsNone(self.read(pd.concat([frame, frame[['trade_date']]], axis=1)))

    def test_empty_and_none(self):
        for frame in [None, pd.DataFrame(), pd.DataFrame(columns=['ts_code', 'trade_date'])]:
            with self.subTest(frame=frame):
                self.assertIsNone(self.read(frame))

    def test_rejected_index_does_not_discard_valid_other_indices(self):
        self.fetcher._api.index_daily.side_effect = lambda **kwargs: pd.DataFrame([
            row(code='wrong' if kwargs['ts_code'] == CODES[0] else kwargs['ts_code'])
        ])
        self.assertEqual([result['name'] for result in self.fetcher.get_main_indices()], NAMES[1:])

    def test_existing_region_and_unavailable_api_guards(self):
        self.assertIsNone(self.fetcher.get_main_indices('us'))
        self.fetcher._api.index_daily.assert_not_called()
        self.fetcher._api = None
        self.assertIsNone(self.fetcher.get_main_indices())


if __name__ == '__main__':
    unittest.main()
