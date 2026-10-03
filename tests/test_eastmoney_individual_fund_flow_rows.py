"""Offline contract tests for the EastMoney individual daily fund-flow table."""

import sys
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from data_provider.fundamental_adapter import AkshareFundamentalAdapter


class TestIndividualFundFlowRows(unittest.TestCase):
    def _read(self, frame):
        sdk = SimpleNamespace(
            stock_individual_fund_flow=Mock(return_value=frame),
            stock_sector_fund_flow_rank=Mock(return_value=pd.DataFrame()),
            stock_sector_fund_flow_summary=Mock(return_value=pd.DataFrame()),
        )
        with patch.dict(sys.modules, {"akshare": sdk}):
            result = AkshareFundamentalAdapter().get_capital_flow("600519.SH")
        sdk.stock_individual_fund_flow.assert_called_once_with(stock="600519", market="sh")
        return result

    def test_selects_unique_latest_date_and_exact_net_amount_column(self):
        frame = pd.DataFrame({
            "日期": [date(2026, 9, 24), date(2026, 9, 25)],
            "主力净流入-净占比": [99.0, 88.0],
            "主力净流入-净额": [1.0, -2.0],
            "5日净流入": [55.0, 66.0],
        })
        flow = self._read(frame)["stock_flow"]
        self.assertEqual(flow["main_net_inflow"], -2.0)
        self.assertEqual(flow["trade_date"], "2026-09-25")
        self.assertIsNone(flow["inflow_5d"])
        self.assertIsNone(flow["inflow_10d"])
        self.assertIsNone(flow["source_available_at"])
        self.assertEqual(flow["amount_unit"], "unknown")

    def test_latest_day_is_independent_of_row_order(self):
        frame = pd.DataFrame({
            "日期": ["2026-09-25", "2026-09-24"],
            "主力净流入-净额": [0.0, 12.0],
        })
        flow = self._read(frame)["stock_flow"]
        self.assertEqual(flow["trade_date"], "2026-09-25")
        self.assertEqual(flow["main_net_inflow"], 0.0)

    def test_bad_daily_tables_do_not_publish_a_stock_flow(self):
        cases = {
            "duplicate_date": pd.DataFrame({
                "日期": ["2026-09-25", "2026-09-25"], "主力净流入-净额": [1.0, 2.0],
            }),
            "invalid_date": pd.DataFrame({
                "日期": ["2026-09-25", "bad"], "主力净流入-净额": [1.0, 2.0],
            }),
            "sdk_nat_date": pd.DataFrame({
                "日期": [date(2026, 9, 25), pd.NaT], "主力净流入-净额": [1.0, 2.0],
            }),
            "missing_amount": pd.DataFrame({
                "日期": ["2026-09-25"], "主力净流入-净占比": [99.0],
            }),
            "latest_nan": pd.DataFrame({
                "日期": ["2026-09-24", "2026-09-25"], "主力净流入-净额": [1.0, float("nan")],
            }),
            "huge_amount": pd.DataFrame({
                "日期": ["2026-09-25"],
                "主力净流入-净额": pd.Series([10 ** 500], dtype=object),
            }),
            "duplicate_column": pd.DataFrame(
                [["2026-09-25", 1.0, 2.0]],
                columns=["日期", "主力净流入-净额", "主力净流入-净额"],
            ),
        }
        for name, frame in cases.items():
            with self.subTest(name=name):
                result = self._read(frame)
                self.assertEqual(result["stock_flow"], {})
                self.assertEqual(result["status"], "not_supported")
                self.assertIn("capital_stock:invalid_daily_rows", result["errors"])


if __name__ == "__main__":
    unittest.main()
