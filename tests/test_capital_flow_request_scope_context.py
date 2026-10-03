"""Manager-to-SDK request scope tests without provider access."""

import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from data_provider.base import DataFetcherManager


class TestCapitalFlowScopeContext(unittest.TestCase):
    def _environment(self, stack, cache_ttl=0):
        config = SimpleNamespace(
            enable_fundamental_pipeline=True,
            fundamental_cache_ttl_seconds=cache_ttl,
            fundamental_cache_max_entries=16,
            fundamental_stage_timeout_seconds=10.0,
            fundamental_fetch_timeout_seconds=5.0,
            fundamental_retry_max=1,
        )
        stack.enter_context(patch("src.config.get_config", return_value=config))
        stack.enter_context(patch("data_provider.fundamental_valuation.read_current_stock_valuation",
                                  side_effect=ValueError("offline valuation fixture")))
        with patch.object(DataFetcherManager, "_init_default_fetchers"):
            manager = DataFetcherManager(fetchers=[])
        sdk = SimpleNamespace(
            stock_individual_fund_flow=Mock(return_value=pd.DataFrame({
                "日期": ["2026-09-25"], "主力净流入-净额": [1.0],
            })),
            stock_main_fund_flow=Mock(side_effect=AssertionError("stock fallback")),
            stock_sector_fund_flow_rank=Mock(return_value=pd.DataFrame({
                "名称": ["行业"], "今日主力净流入-净额": [1.0],
            })),
        )
        stack.enter_context(patch.dict(sys.modules, {"akshare": sdk}))
        quote = stack.enter_context(patch.object(manager, "get_realtime_quote", return_value=None))
        bundle = stack.enter_context(patch.object(manager._fundamental_adapter, "get_fundamental_bundle", return_value={}))
        dragon = stack.enter_context(patch.object(manager, "get_dragon_tiger_context", return_value={}))
        boards = stack.enter_context(patch.object(manager, "get_board_context", return_value={}))
        return manager, sdk, (quote, bundle, dragon, boards)

    def _capital_block(self, manager, path, symbol):
        if path == "full":
            return manager.get_fundamental_context(symbol)["capital_flow"]
        return manager.get_capital_flow_context(symbol)

    def test_both_context_paths_preserve_explicit_scope(self):
        for path in ("full", "direct"):
            for symbol, market in (("600519.SH", "sh"), ("000001.SZ", "sz")):
                with self.subTest(path=path, symbol=symbol), ExitStack() as stack:
                    manager, sdk, other_getters = self._environment(stack)
                    block = self._capital_block(manager, path, symbol)
                    sdk.stock_individual_fund_flow.assert_called_once_with(stock=symbol[:6], market=market)
                    sdk.stock_main_fund_flow.assert_not_called()
                    self.assertEqual(block["data"]["stock_flow"]["main_net_inflow"], 1.0)
                    if path == "full":
                        for getter in other_getters:
                            self.assertEqual(getter.call_args.args[0], symbol[:6])

    def test_missing_scope_never_hides_behind_sector_success(self):
        for path in ("full", "direct"):
            for symbol in ("600519", "000001", "600519.XX", "600519.SH.SZ", "600519.sh", "SH600519"):
                with self.subTest(path=path, symbol=symbol), ExitStack() as stack:
                    manager, sdk, _ = self._environment(stack)
                    block = self._capital_block(manager, path, symbol)
                    sdk.stock_individual_fund_flow.assert_not_called()
                    sdk.stock_main_fund_flow.assert_not_called()
                    self.assertEqual(block["data"]["stock_flow"], {})
                    self.assertTrue(block["data"]["sector_rankings"]["top"])
                    self.assertIn("capital_stock:missing_explicit_exchange", block["errors"])

    def test_full_context_cache_isolates_original_scope_both_directions(self):
        for symbols in (("600519.SH", "600519"), ("600519", "600519.SH"),
                        ("000001.SH", "000001.SZ")):
            with self.subTest(symbols=symbols), ExitStack() as stack:
                manager, sdk, _ = self._environment(stack, cache_ttl=60)
                stack.enter_context(patch.object(manager, "_should_cache_fundamental_context", return_value=True))
                legacy_key = manager._get_fundamental_cache_key(symbols[0], 10.0)
                import time
                manager._fundamental_cache[legacy_key] = {"ts": time.time(), "context": {"legacy": True}}
                blocks = [self._capital_block(manager, "full", symbol) for symbol in symbols]
                for symbol, block in zip(symbols, blocks):
                    if "." not in symbol:
                        self.assertEqual(block["data"]["stock_flow"], {})
                    else:
                        self.assertEqual(block["data"]["stock_flow"]["main_net_inflow"], 1.0)
                self.assertEqual(len(manager._fundamental_cache), 3)
                expected_markets = [symbol[-2:].lower() for symbol in symbols if "." in symbol]
                self.assertEqual([call.kwargs["market"] for call in sdk.stock_individual_fund_flow.call_args_list],
                                 expected_markets)
                for symbol in symbols:
                    self._capital_block(manager, "full", symbol)
                self.assertEqual(sdk.stock_individual_fund_flow.call_count, len(expected_markets))

    def test_non_cn_context_keeps_existing_normalized_route(self):
        with ExitStack() as stack:
            manager, sdk, _ = self._environment(stack)
            offshore = stack.enter_context(patch.object(manager, "_build_offshore_fundamental_context",
                                                        return_value={"offshore": True}))
            self.assertEqual(manager.get_fundamental_context("1810.HK"), {"offshore": True})
            offshore.assert_called_once_with("HK01810", market="hk", budget_seconds=None)
            sdk.stock_individual_fund_flow.assert_not_called()
            self.assertEqual(manager._fundamental_cache, {})
