import os
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.efinance_fetcher import EfinanceFetcher


class TestEfinanceMainIndices(unittest.TestCase):
    def _get_indices(self, frame):
        fetcher = EfinanceFetcher()
        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(get_realtime_quotes=lambda *args, **kwargs: frame)
        )
        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                return fetcher.get_main_indices(region="cn")

    def test_get_main_indices_skips_duplicate_target_and_keeps_other_unique_index(self):
        frame = pd.DataFrame(
            {
                "股票代码": ["000001", "000001", "399001"],
                "行情ID": ["1.000001", "1.000001", "0.399001"],
                "最新价": [3200.0, 3201.0, 12000.0],
            }
        )

        data = self._get_indices(frame)

        self.assertIsNotNone(data)
        self.assertEqual([item["code"] for item in data], ["sz399001"])
        self.assertEqual(data[0]["current"], 12000.0)

    def test_get_main_indices_rejects_wrong_or_missing_code(self):
        for frame in (
            pd.DataFrame({"股票代码": ["999999"], "行情ID": ["1.999999"], "最新价": [100.0]}),
            pd.DataFrame({"最新价": [100.0]}),
        ):
            with self.subTest(columns=list(frame.columns)):
                self.assertIsNone(self._get_indices(frame))

    def test_get_main_indices_accepts_unique_code_fallback_column(self):
        frame = pd.DataFrame({"code": ["000001"], "行情ID": ["1.000001"], "price": [3200.0]})

        data = self._get_indices(frame)

        self.assertIsNotNone(data)
        self.assertEqual([item["code"] for item in data], ["sh000001"])

    def test_get_main_indices_rejects_wrong_market_or_missing_quote_id(self):
        for frame in (
            pd.DataFrame({"股票代码": ["000001"], "行情ID": ["0.000001"]}),
            pd.DataFrame({"股票代码": ["000001"]}),
            pd.DataFrame({"股票代码": ["999999"], "行情ID": ["1.000001"]}),
        ):
            with self.subTest(columns=list(frame.columns)):
                self.assertIsNone(self._get_indices(frame))

    def test_get_main_indices_selects_full_identity_among_same_code_markets(self):
        frame = pd.DataFrame(
            {
                "股票代码": ["000001", "000001"],
                "行情ID": ["0.000001", "1.000001"],
                "最新价": [100.0, 3200.0],
            }
        )

        data = self._get_indices(frame)

        self.assertIsNotNone(data)
        self.assertEqual([item["code"] for item in data], ["sh000001"])
        self.assertEqual(data[0]["current"], 3200.0)

    def test_get_main_indices_prefers_jinkai_column_for_open_price(self):
        fetcher = EfinanceFetcher()
        fake_df = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "行情ID": ["1.000001"],
                "最新价": [3200.0],
                "涨跌幅": [0.63],
                "涨跌额": [20.0],
                "今开": [3188.0],
                "开盘": [0.0],
                "最高": [3215.0],
                "最低": [3170.0],
                "成交量": [123456789],
                "成交额": [9876543210.0],
                "振幅": [1.2],
            }
        )
        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(get_realtime_quotes=lambda *args, **kwargs: fake_df)
        )

        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                data = fetcher.get_main_indices(region="cn")

        self.assertIsNotNone(data)
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["code"], "sh000001")
        self.assertEqual(data[0]["name"], "上证指数")
        self.assertAlmostEqual(data[0]["open"], 3188.0)
        self.assertAlmostEqual(data[0]["current"], 3200.0)

    def test_get_main_indices_falls_back_to_kaipan_when_jinkai_is_missing(self):
        fetcher = EfinanceFetcher()
        fake_df = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "行情ID": ["1.000001"],
                "最新价": [3200.0],
                "涨跌幅": [0.63],
                "涨跌额": [20.0],
                "今开": [""],
                "开盘": [3186.0],
                "最高": [3215.0],
                "最低": [3170.0],
                "成交量": [123456789],
                "成交额": [9876543210.0],
                "振幅": [1.2],
            }
        )
        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(get_realtime_quotes=lambda *args, **kwargs: fake_df)
        )

        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                data = fetcher.get_main_indices(region="cn")

        self.assertIsNotNone(data)
        self.assertEqual(len(data), 1)
        self.assertAlmostEqual(data[0]["open"], 3186.0)


if __name__ == "__main__":
    unittest.main()
