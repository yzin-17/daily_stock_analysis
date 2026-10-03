"""Offline current-industry fund-flow ranking contract tests."""

import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from data_provider.fundamental_adapter import AkshareFundamentalAdapter


class TestIndustryFundFlowRank(unittest.TestCase):
    def _read(self, rank, top_n=1):
        rank_call = Mock(side_effect=rank) if isinstance(rank, Exception) else Mock(return_value=rank)
        sdk = SimpleNamespace(
            stock_individual_fund_flow=Mock(side_effect=AssertionError("unexpected stock request")),
            stock_sector_fund_flow_rank=rank_call,
            stock_sector_fund_flow_summary=Mock(return_value=pd.DataFrame({
                "代码": ["000001"], "名称": ["个股"], "今日主力净流入-净额": [999.0],
            })),
        )
        with patch.dict(sys.modules, {"akshare": sdk}):
            result = AkshareFundamentalAdapter().get_capital_flow("600519", top_n=top_n)
        sdk.stock_individual_fund_flow.assert_not_called()
        return result, sdk

    def test_explicit_industry_today_request_uses_exact_amount_not_ratio(self):
        frame = pd.DataFrame({
            "名称": ["行业甲", "行业乙", "行业丙"],
            "今日主力净流入-净占比": [99.0, 1.0, 50.0],
            "今日主力净流入-净额": [2.0, -3.0, float("nan")],
        })
        result, sdk = self._read(frame)
        sdk.stock_sector_fund_flow_rank.assert_called_once_with(
            indicator="今日", sector_type="行业资金流",
        )
        sdk.stock_sector_fund_flow_summary.assert_not_called()
        self.assertEqual(result["sector_rankings"]["top"], [{"name": "行业甲", "net_inflow": 2.0}])
        self.assertEqual(result["sector_rankings"]["bottom"], [{"name": "行业乙", "net_inflow": -3.0}])
        self.assertIn("capital_sector:stock_sector_fund_flow_rank", result["source_chain"])

    def test_documented_exact_amount_label_is_unambiguous(self):
        result, _ = self._read(pd.DataFrame({
            "名称": ["行业甲"], "主力净流入-净额": [4.0],
        }))
        self.assertEqual(result["sector_rankings"]["top"], [{"name": "行业甲", "net_inflow": 4.0}])

    def test_malformed_or_stock_summary_shapes_do_not_publish_industry_rank(self):
        cases = {
            "ratio_only": pd.DataFrame({"名称": ["行业甲"], "今日主力净流入-净占比": [99.0]}),
            "stock_rows": pd.DataFrame({
                "代码": ["000001"], "名称": ["个股"], "今日主力净流入-净额": [2.0],
            }),
            "duplicate_name": pd.DataFrame({
                "名称": ["行业甲", " 行业甲 "], "今日主力净流入-净额": [2.0, 3.0],
            }),
            "no_amount": pd.DataFrame({"名称": ["行业甲"], "今日主力净流入-净额": [float("nan")]}),
            "ambiguous_amount": pd.DataFrame({
                "名称": ["行业甲"], "今日主力净流入-净额": [2.0], "主力净流入-净额": [3.0],
            }),
        }
        for name, frame in cases.items():
            with self.subTest(name=name):
                result, sdk = self._read(frame)
                self.assertEqual(result["sector_rankings"], {"top": [], "bottom": []})
                self.assertIn("capital_sector:invalid_rank_rows", result["errors"])
                sdk.stock_sector_fund_flow_summary.assert_not_called()

    def test_rank_failure_does_not_fall_back_to_one_industry_stock_rows(self):
        result, sdk = self._read(RuntimeError("offline ranking failure"))
        self.assertEqual(result["sector_rankings"], {"top": [], "bottom": []})
        self.assertIn("stock_sector_fund_flow_rank:RuntimeError", result["errors"])
        sdk.stock_sector_fund_flow_summary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
