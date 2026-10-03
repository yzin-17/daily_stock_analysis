"""真实持仓转换不能用抓取时刻伪造披露日期。"""

import pandas as pd

from api.thesis_ledger import _fund_holdings_payload


def test_quarter_only_holdings_keep_disclosure_time_unknown():
    frame = pd.DataFrame({'股票代码': ['600519'], '股票名称': ['贵州茅台'],
                          '占净值比例': [8], '季度': ['2026年2季度']})
    result = _fund_holdings_payload('000001.OF', frame, 'akshare', False)
    assert result['disclosureDate'] is None
    assert result['fetchedAt']
    assert result['reportPeriod'] == '2026-Q2'
    assert result['holdings'][0]['weight'] == 0.08
