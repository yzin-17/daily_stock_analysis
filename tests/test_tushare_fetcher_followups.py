# -*- coding: utf-8 -*-
"""Regression tests for post-merge Tushare follow-up fixes."""

import importlib.util
import sys
import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

try:
    json_repair_available = importlib.util.find_spec("json_repair") is not None
except ValueError:
    json_repair_available = "json_repair" in sys.modules

if not json_repair_available and "json_repair" not in sys.modules:
    sys.modules["json_repair"] = MagicMock()

from data_provider.tushare_fetcher import TushareFetcher
from data_provider.tushare_http_client import TushareApiError


class TestTushareFetcherFollowUps(unittest.TestCase):
    """Cover rate limiting and cross-day trade-calendar refresh behavior."""

    @staticmethod
    def _make_fetcher() -> TushareFetcher:
        with patch.object(TushareFetcher, "_init_api", return_value=None):
            fetcher = TushareFetcher()
        fetcher._api = MagicMock()
        fetcher.priority = 2
        return fetcher

    def test_get_trade_time_refreshes_trade_calendar_when_day_changes(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.side_effect = [
            pd.DataFrame({"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}),
            pd.DataFrame({"cal_date": ["20260318", "20260317"], "is_open": [1, 1]}),
        ]

        with patch.object(
            fetcher,
            "_get_china_now",
            side_effect=[
                datetime(2026, 3, 17, 20, 0),
                datetime(2026, 3, 17, 20, 0),
                datetime(2026, 3, 18, 20, 0),
                datetime(2026, 3, 18, 20, 0),
            ],
        ), patch.object(fetcher, "_check_rate_limit") as rate_limit_mock:
            self.assertEqual(fetcher.get_trade_time(early_time="00:00", late_time="19:00"), "20260317")
            self.assertEqual(fetcher.get_trade_time(early_time="00:00", late_time="19:00"), "20260318")

        self.assertEqual(fetcher._api.trade_cal.call_count, 2)
        self.assertEqual(rate_limit_mock.call_count, 2)
    def test_get_trade_time_returns_latest_trade_date_on_non_trade_day(self) -> None:
        """Non-trade day (e.g. Saturday) should return the most recent trade
        date (Friday), not the one before it (Thursday).  Fixes #1009."""
        fetcher = self._make_fetcher()
        # 2026-03-21 is Saturday; Friday 20 and Thursday 19 are trade dates
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {
                "cal_date": ["20260314", "20260315", "20260316",
                             "20260317", "20260318", "20260319",
                             "20260320", "20260321"],
                "is_open": [0, 0, 1, 1, 1, 1, 1, 0],
            }
        )

        with patch.object(
            fetcher,
            "_get_china_now",
            # called twice: once by get_trade_time, once by _get_trade_dates
            side_effect=[datetime(2026, 3, 21, 10, 0)] * 2,
        ), patch.object(fetcher, "_check_rate_limit"):
            result = fetcher.get_trade_time(early_time="00:00", late_time="19:00")

        # Should be Friday (20th), NOT Thursday (19th)
        self.assertEqual(result, "20260320")

    def test_get_trade_time_trade_day_before_data_ready_returns_previous(self) -> None:
        """On a trade day within the early-late window, should return the
        previous trade date (data not ready yet for today)."""
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {
                "cal_date": ["20260319", "20260320"],
                "is_open": [1, 1],
            }
        )

        with patch.object(
            fetcher,
            "_get_china_now",
            # Friday 10:00 AM - within 00:00~19:00 window, data not ready
            side_effect=[datetime(2026, 3, 20, 10, 0)] * 2,
        ), patch.object(fetcher, "_check_rate_limit"):
            result = fetcher.get_trade_time(early_time="00:00", late_time="19:00")

        # Data not ready, should fall back to Thursday (19th)
        self.assertEqual(result, "20260319")
        
          
    def test_get_sector_rankings_rate_limits_calendar_and_rankings_api(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.moneyflow_ind_ths.return_value = pd.DataFrame(
            {
                "industry": ["AI", "消费"],
                "pct_change": [1.8, -0.6],
            }
        )

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 16, 0)), patch.object(
            fetcher, "_check_rate_limit"
        ) as rate_limit_mock:
            top, bottom = fetcher.get_sector_rankings(n=1)

        self.assertEqual(
            top,
            [{"name": "AI", "change_pct": 1.8, "source": "tushare/ths:moneyflow_ind_ths"}],
        )
        self.assertEqual(
            bottom,
            [{"name": "消费", "change_pct": -0.6, "source": "tushare/ths:moneyflow_ind_ths"}],
        )
        self.assertEqual(rate_limit_mock.call_count, 2)

    def test_get_sector_rankings_fallback_keeps_dc_source(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.moneyflow_ind_ths.return_value = pd.DataFrame(
            {"industry": ["重复", "重复"], "pct_change": [1.0, 2.0]}
        )
        fetcher._api.moneyflow_ind_dc.return_value = pd.DataFrame(
            {
                "content_type": ["行业", "行业", "概念"],
                "name": ["银行", "科技", "忽略"],
                "pct_change": [-0.5, 1.2, 9.9],
            }
        )

        with patch.object(
            fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 16, 0)
        ), patch.object(fetcher, "_check_rate_limit"):
            top, bottom = fetcher.get_sector_rankings(n=1)

        self.assertEqual(top[0]["source"], "tushare/eastmoney:moneyflow_ind_dc")
        self.assertEqual(bottom[0]["source"], "tushare/eastmoney:moneyflow_ind_dc")
        fetcher._api.moneyflow_ind_ths.assert_called_once_with(trade_date="20260317")
        fetcher._api.moneyflow_ind_dc.assert_called_once_with(trade_date="20260317")

    def test_get_sector_rankings_before_1530_uses_previous_trade_date(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260316"], "is_open": [1, 1]}
        )
        fetcher._api.moneyflow_ind_ths.return_value = pd.DataFrame(
            {"industry": ["AI"], "pct_change": [0.8]}
        )

        with patch.object(
            fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 15, 0)
        ), patch.object(fetcher, "_check_rate_limit"):
            top, _ = fetcher.get_sector_rankings(n=1)

        self.assertEqual(top[0]["source"], "tushare/ths:moneyflow_ind_ths")
        fetcher._api.moneyflow_ind_ths.assert_called_once_with(trade_date="20260316")
        fetcher._api.moneyflow_ind_dc.assert_not_called()

    def test_get_chip_distribution_rate_limits_all_tushare_calls(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.trade_cal.return_value = pd.DataFrame(
            {"cal_date": ["20260317", "20260314"], "is_open": [1, 1]}
        )
        fetcher._api.cyq_chips.return_value = pd.DataFrame(
            {
                "price": [9.0, 10.0, 11.0],
                "percent": [20.0, 50.0, 30.0],
            }
        )
        fetcher._api.daily.return_value = pd.DataFrame({"close": [10.5]})

        with patch.object(fetcher, "_get_china_now", return_value=datetime(2026, 3, 17, 20, 0)), patch.object(
            fetcher, "_check_rate_limit"
        ) as rate_limit_mock:
            chip = fetcher.get_chip_distribution("600519")

        self.assertIsNotNone(chip)
        if chip is None:
            self.fail("expected chip distribution data")
        self.assertEqual(chip.date, "2026-03-17")
        self.assertAlmostEqual(chip.profit_ratio, 0.7)
        self.assertAlmostEqual(chip.avg_cost, 10.1)
        self.assertAlmostEqual(chip.concentration_90, 0.1)
        self.assertAlmostEqual(chip.concentration_70, 0.1)
        self.assertEqual(rate_limit_mock.call_count, 3)

    def test_convert_stock_code_accepts_exchange_prefixed_a_share(self) -> None:
        fetcher = self._make_fetcher()

        self.assertEqual(fetcher._convert_stock_code("SZ000001"), "000001.SZ")
        self.assertEqual(fetcher._convert_stock_code("SH600519"), "600519.SH")
        self.assertEqual(fetcher._convert_stock_code("605218"), "605218.SH")
        self.assertEqual(fetcher._convert_stock_code("600519.SS"), "600519.SH")

    @patch.dict(sys.modules, {"tushare": MagicMock()})
    def test_legacy_realtime_quote_keeps_sz_hint_as_stock_symbol(self) -> None:
        fetcher = self._make_fetcher()
        fetcher._api.quotation.side_effect = Exception("quota")

        tushare_module = sys.modules["tushare"]
        tushare_module.get_realtime_quotes.return_value = pd.DataFrame(
            [
                {
                    "name": "平安银行",
                    "price": "10.94",
                    "pre_close": "10.88",
                    "volume": "1000",
                    "amount": "2000",
                    "high": "11.00",
                    "low": "10.80",
                    "open": "10.90",
                }
            ]
        )

        quote = fetcher.get_realtime_quote("SZ000001")

        self.assertIsNotNone(quote)
        self.assertEqual(quote.code, "000001")
        self.assertEqual(quote.name, "平安银行")
        tushare_module.get_realtime_quotes.assert_called_once_with("000001")


@pytest.mark.parametrize("amount", [None, "invalid", "Infinity", -1])
def test_market_stats_rejects_unknown_active_turnover(amount):
    fetcher = TestTushareFetcherFollowUps._make_fetcher()
    frame = pd.DataFrame([
        dict(ts_code="600519.SH", name="贵州茅台", close="11", pre_close="10", amount="100"),
        dict(ts_code="000001.SZ", name="平安银行", close=9, pre_close=10, amount=amount),
    ])
    assert fetcher._calc_market_stats(frame) is None
    frame.loc[1, ["close", "pre_close", "amount"]] = [None, None, "0"]
    assert fetcher._calc_market_stats(frame, amount_unit=1000) == {
        "up_count": 1, "down_count": 0, "flat_count": 0, "limit_up_count": 1,
        "limit_down_count": 0, "total_amount": pytest.approx(0.001),
    }


@pytest.mark.parametrize("price,percent", [(0, 50), (float("inf"), 50), (9, -1), (9, 0), (9, None)])
def test_invalid_chip_rows_do_not_call_daily_or_become_empty(price, percent):
    fetcher = TestTushareFetcherFollowUps._make_fetcher()
    fetcher.get_trade_time = MagicMock(return_value="20260317")
    fetcher._api.cyq_chips.return_value = pd.DataFrame({
        "ts_code": ["600519.SH"], "trade_date": ["20260317"], "price": [price], "percent": [percent],
    })
    assert fetcher.get_chip_distribution("600519") is None
    with pytest.raises(TushareApiError) as error:
        fetcher.get_chip_distribution("600519.SH", strict=True)
    assert error.value.code == "invalid_response"
    fetcher._api.daily.assert_not_called()


@pytest.mark.parametrize("price,pre_close", [("bad", "10"), ("11", None), ("11", "Infinity")])
def test_legacy_quote_never_fabricates_a_zero_change(price, pre_close):
    fetcher = TestTushareFetcherFollowUps._make_fetcher()
    fetcher._api.quotation.return_value = pd.DataFrame({"price": [None], "pre_close": [10]})
    sdk = MagicMock()
    sdk.get_realtime_quotes.return_value = pd.DataFrame({"price": [price], "pre_close": [pre_close]})
    with patch.dict(sys.modules, {"tushare": sdk}):
        assert fetcher.get_realtime_quote("600519") is None
    sdk.get_realtime_quotes.assert_called_once()
