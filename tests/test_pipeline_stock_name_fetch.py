"""原生分析流水线的名称读取集成断言，与纯数据测试分别收集。"""

from unittest.mock import MagicMock

import pandas as pd

from src.core.pipeline import StockAnalysisPipeline


def test_fetch_and_save_stock_data_uses_lightweight_name_lookup():
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.fetcher_manager = MagicMock()
    pipeline.db = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.db.has_today_data.return_value = False
    pipeline.fetcher_manager.get_daily_data.return_value = (
        pd.DataFrame([{
            "date": "2026-03-27", "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
            "volume": 1, "amount": 1.0, "pct_chg": 0.0,
        }]),
        "dummy",
    )
    pipeline.db.save_daily_data.return_value = 1

    success, error = StockAnalysisPipeline.fetch_and_save_stock_data(pipeline, "600519")

    assert success
    assert error is None
    pipeline.fetcher_manager.get_stock_name.assert_called_once_with("600519", allow_realtime=False)
