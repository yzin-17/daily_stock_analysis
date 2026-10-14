"""真实持仓转换不能用抓取时刻伪造披露日期。"""

import pandas as pd

from api.thesis_ledger import _fund_holdings_payload
from data_provider.eastmoney_holdings_projection import holdings_result
from src.services.thesis_ledger_fund_holdings_contract_v1 import HoldingsRequest


def test_quarter_only_holdings_keep_disclosure_time_unknown():
    frame = pd.DataFrame({'股票代码': ['600519'], '股票名称': ['贵州茅台'],
                          '占净值比例': [8], '季度': ['2026年2季度']})
    value = holdings_result(frame, HoldingsRequest(symbol='000001.OF'), 'akshare', 'eastmoney')
    result = _fund_holdings_payload('000001.OF', value, 'akshare', False)
    assert result['disclosureDate'] is None
    assert result['fetchedAt']
    assert result['reportPeriod'] == '2026-Q2'
    assert result['holdings'][0]['weight'] == 0.08
