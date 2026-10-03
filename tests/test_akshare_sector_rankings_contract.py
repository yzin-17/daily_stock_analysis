import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.sector_rankings_contract import (
    SectorRankingContractError,
    normalize_sector_rankings,
)


def make_fetcher():
    fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
    fetcher._set_random_user_agent = lambda: None
    fetcher._enforce_rate_limit = lambda: None
    return fetcher


def test_eastmoney_sector_rankings_keep_exact_source(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_board_industry_name_em=lambda: pd.DataFrame(
                {"板块名称": ["半导体", "银行"], "涨跌幅": [2.5, -1.2]}
            ),
            stock_sector_spot=lambda **_kwargs: pytest.fail("sina fallback should not run"),
        ),
    )
    top, bottom = make_fetcher().get_sector_rankings(1)
    assert top == [{"name": "半导体", "change_pct": 2.5, "source": "akshare/eastmoney:stock_board_industry_name_em"}]
    assert bottom == [{"name": "银行", "change_pct": -1.2, "source": "akshare/eastmoney:stock_board_industry_name_em"}]


def test_sina_fallback_keeps_exact_source(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_board_industry_name_em=lambda: (_ for _ in ()).throw(RuntimeError("eastmoney down")),
            stock_sector_spot=lambda **kwargs: pd.DataFrame(
                {"板块": ["煤炭", "医药"], "涨跌幅": [1.0, -0.5]}
            ) if kwargs == {"indicator": "行业"} else pytest.fail("wrong sina request"),
        ),
    )
    top, bottom = make_fetcher().get_sector_rankings(1)
    assert top[0]["source"] == "akshare/sina:stock_sector_spot"
    assert bottom[0]["source"] == "akshare/sina:stock_sector_spot"


def test_bad_eastmoney_contract_can_fall_back_but_never_masquerades(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_board_industry_name_em=lambda: pd.DataFrame(
                {"板块名称": ["重复", "重复"], "涨跌幅": [2.0, 1.0]}
            ),
            stock_sector_spot=lambda **_kwargs: pd.DataFrame(
                {"板块": ["新浪行业"], "涨跌幅": [0.3]}
            ),
        ),
    )
    top, bottom = make_fetcher().get_sector_rankings(1)
    assert top == [{"name": "新浪行业", "change_pct": 0.3, "source": "akshare/sina:stock_sector_spot"}]
    assert bottom == top


@pytest.mark.parametrize(
    "frame,error_code",
    [
        (pd.DataFrame({"名称": ["行业"], "涨跌幅": [1]}), "missing_sector_columns"),
        (pd.DataFrame({"板块名称": ["", "行业"], "涨跌幅": [1, 2]}), "invalid_sector_identity"),
        (pd.DataFrame({"板块名称": ["行业"], "涨跌幅": ["bad"]}), "missing_sector_change"),
    ],
)
def test_sector_contract_rejects_ambiguous_or_unusable_rows(frame, error_code):
    with pytest.raises(SectorRankingContractError, match=error_code):
        normalize_sector_rankings(
            frame,
            name_column="板块名称",
            change_column="涨跌幅",
            source="akshare/eastmoney:stock_board_industry_name_em",
            n=1,
        )


def test_sector_contract_drops_only_non_numeric_change_rows():
    result = normalize_sector_rankings(
        pd.DataFrame(
            {"板块名称": ["有效", "无效"], "涨跌幅": [1.2, None]}
        ),
        name_column="板块名称",
        change_column="涨跌幅",
        source="akshare/eastmoney:stock_board_industry_name_em",
        n=2,
    )
    assert result == (
        [{"name": "有效", "change_pct": 1.2, "source": "akshare/eastmoney:stock_board_industry_name_em"}],
        [{"name": "有效", "change_pct": 1.2, "source": "akshare/eastmoney:stock_board_industry_name_em"}],
    )


def test_concept_rankings_keep_exact_eastmoney_source(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_board_concept_name_em=lambda: pd.DataFrame(
                {"板块名称": ["机器人", "低空经济"], "涨跌幅": [3.1, -0.2]}
            ),
        ),
    )
    top, bottom = make_fetcher().get_concept_rankings(1)
    assert top == [
        {
            "name": "机器人",
            "change_pct": 3.1,
            "source": "akshare/eastmoney:stock_board_concept_name_em",
        }
    ]
    assert bottom[0]["source"] == "akshare/eastmoney:stock_board_concept_name_em"


def test_concept_rankings_reject_duplicate_identity(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_board_concept_name_em=lambda: pd.DataFrame(
                {"板块名称": ["重复概念", "重复概念"], "涨跌幅": [1.0, 2.0]}
            ),
        ),
    )
    assert make_fetcher().get_concept_rankings(2) is None
