"""原生产品仍保留期间的迁移兼容断言；纯数据基线继续由独立测试所有。"""

import os

import pytest

from tests.test_data_indicator_kernels import FIXTURE, digest, frame_for
from src.data_config import get_data_config
from src.market_context import get_market_guidelines
from src.core.trading_calendar import get_market_for_stock


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["name"])
def test_native_wrappers_preserve_original_kernel_values(case):
    from src.stock_analyzer import StockTrendAnalyzer
    analyzer = StockTrendAnalyzer()
    for key, attribute in (("fast", "MACD_FAST"), ("slow", "MACD_SLOW"), ("signal", "MACD_SIGNAL"),
                           ("short", "RSI_SHORT"), ("mid", "RSI_MID"), ("long", "RSI_LONG")):
        setattr(analyzer, attribute, case["parameters"][key])
    calculated = analyzer._calculate_rsi(analyzer._calculate_macd(analyzer._calculate_mas(frame_for(case))))
    assert digest(calculated[case["columns"]].values.tolist()) == case["valuesDigest"]


def test_legacy_reload_also_invalidates_data_configuration(tmp_path, monkeypatch):
    from src.config import Config
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    with monkeypatch.context() as isolated:
        isolated.setattr(os, "environ", {"ENV_FILE": str(env_file), "TICKFLOW_API_KEY": "fixture-before"})
        Config.reset_instance()
        try:
            assert get_data_config().tickflow_api_key == "fixture-before"
            os.environ["TICKFLOW_API_KEY"] = "fixture-after"
            Config.reset_instance()
            assert get_data_config().tickflow_api_key == "fixture-after"
        finally:
            Config.reset_instance()


def test_market_guidelines_for_jp_kr_exclude_a_share_specific_context() -> None:
    jp_guidelines = get_market_guidelines("7203.T")
    kr_guidelines = get_market_guidelines("005930.KS")

    assert "日股" in jp_guidelines
    assert "韩股" in kr_guidelines
    for text in (jp_guidelines, kr_guidelines):
        assert "不要套用 A 股" in text
        assert "北向资金" in text
        assert "龙虎榜" in text


def test_market_guidelines_for_tw_keep_taiwan_context() -> None:
    tw_guidelines = get_market_guidelines("2330.TW")

    assert "台股" in tw_guidelines
    # Taiwan keeps its own positive framing (±10% limit + three institutional
    # groups); do NOT copy the JP/KR "no A-share price-limit board" wording.
    assert "三大法人" in tw_guidelines
    assert ("±10%" in tw_guidelines) or ("涨跌停" in tw_guidelines)
    # Only China A-share-specific concepts are excluded.
    assert "北向资金" in tw_guidelines
    assert "龙虎榜" in tw_guidelines


def test_tw_is_first_class_on_write_paths() -> None:
    """TW is a first-class market on the decision-signal / portfolio / intelligence
    write paths, matching jp/kr.

    Regression guard: the analysis pipeline auto-extracts a DecisionSignal after
    history save (pipeline._extract_decision_signal_after_history_save). If `tw`
    were absent from VALID_MARKETS, _normalize_market("tw") would raise ValueError
    on that main path and the signal would be silently dropped — making tw the
    only yfinance-supported market that can be analyzed but never produces a signal.
    """
    from src.services.decision_signal_service import DecisionSignalService
    from src.services.portfolio_service import VALID_MARKETS
    from src.services.intelligence_service import _ALLOWED_MARKETS

    assert get_market_for_stock("2330.TW") == "tw"  # data layer recognizes tw
    assert DecisionSignalService._normalize_market("tw") == "tw"
    assert "tw" in VALID_MARKETS
    assert "tw" in _ALLOWED_MARKETS
