"""持仓异常必须显式失败，不能静默丢行或合并权重。"""

import pandas as pd
import pytest
from fastapi import HTTPException

from api.thesis_ledger import _fund_holdings_payload
from src.services.thesis_ledger_holding_rows import holding_period, validate_holding_rows
from src.services.thesis_ledger_provider_runtime import ProviderCallError, ThesisLedgerProviderRuntime


def frame(codes=None, periods=None, weights=None):
    codes = codes or ['600519']
    return pd.DataFrame({'股票代码': codes, '股票名称': ['名称'] * len(codes),
                         '季度': periods or ['2026年2季度'] * len(codes),
                         '占净值比例': weights if weights is not None else [8] * len(codes)})


@pytest.mark.parametrize('period', [None, '未知', '2026年5季度', '12026年2季度',
                                   '2026年1季度 2026年2季度'])
def test_invalid_period_rejects_entire_frame(period):
    rows = frame(['600519', '000001'], ['2026年2季度', period])
    with pytest.raises(ValueError):
        validate_holding_rows(rows)
    with pytest.raises(HTTPException) as error:
        _fund_holdings_payload('000001.OF', rows, 'akshare', False)
    assert error.value.status_code == 502


def test_source_suffix_and_distinct_quarters_are_allowed():
    assert holding_period('2026年2季度股票投资明细') == (2026, 2)
    rows = frame(['600519', '600519'], ['2026年1季度', '2026年2季度'], [7, 8])
    validate_holding_rows(rows)
    result = _fund_holdings_payload('000001.OF', rows, 'akshare', False)
    assert result['reportPeriod'] == '2026-Q2'
    assert len(result['holdings']) == 1
    assert result['holdings'][0]['weight'] == 0.08


def test_duplicate_rows_are_not_summed():
    rows = frame(['600519', '600519'])
    with pytest.raises(ValueError, match='重复'):
        validate_holding_rows(rows)
    with pytest.raises(HTTPException) as error:
        _fund_holdings_payload('000001.OF', rows, 'akshare', False)
    assert error.value.status_code == 502


def test_canonical_duplicate_is_rejected_by_api():
    with pytest.raises(HTTPException) as error:
        _fund_holdings_payload('000001.OF', frame(['600519', '600519.SH']), 'akshare', False)
    assert error.value.status_code == 502


def test_provider_maps_invalid_rows_to_invalid_response():
    with pytest.raises(ProviderCallError) as error:
        ThesisLedgerProviderRuntime._validate_fund_holdings(frame(periods=['未知']))
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('weight', [True, -1, 101, float('nan'), float('inf'), None])
def test_invalid_weight_rejected(weight):
    with pytest.raises(ValueError):
        validate_holding_rows(frame(weights=[weight]))


def test_totals_are_checked_per_period():
    validate_holding_rows(frame(['600519', '000001'], ['2026年1季度', '2026年2季度'], [80, 80]))
    validate_holding_rows(frame(weights=[0]))
    with pytest.raises(ValueError, match='合计'):
        validate_holding_rows(frame(['600519', '000001'], weights=[80, 80]))


@pytest.mark.parametrize('column,value', [('股票代码', ''), ('股票名称', ' '), ('股票代码', 600519)])
def test_identity_required(column, value):
    rows = frame()
    rows[column] = value
    with pytest.raises(ValueError, match='身份'):
        validate_holding_rows(rows)
