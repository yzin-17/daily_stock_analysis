"""通达信原生分钟：有界完整分页，不伪造缺失的历史区间。"""

import pandas as pd
from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from data_provider.intraday_projection import project_intraday

CATEGORIES = {'1m': 8, '5m': 0, '15m': 1, '30m': 2, '60m': 3}


def read_minute(query, context):
    from data_provider.intraday_projection import cn_identity
    code, exchange = cn_identity(query)
    pages, previous = [], None
    with context.adapter._pytdx_session(max_hosts=1, timeout_seconds=min(context.timeout_seconds, 4.5)) as api:
        for offset in range(0, 8000, 800):
            values = api.get_security_bars(CATEGORIES[query.timeframe], 1 if exchange == 'SH' else 0, code, offset, 800)
            if not values:
                break
            frame = pd.DataFrame(values)
            earliest = str(frame['datetime'].min())
            if previous is not None and earliest >= previous:
                raise CapabilityContractError('invalid_response', '通达信分钟分页未向前推进')
            previous = earliest
            pages.append(frame)
            if earliest[:10] < query.start or len(values) < 800:
                break
        else:
            raise CapabilityContractError('insufficient_coverage', '通达信分钟窗口超过分页上限')
    raw = pd.concat(pages, ignore_index=True) if pages else pd.DataFrame()
    return project_intraday(raw, query, 'pytdx', columns={'datetime': 'timestamp', 'vol': 'volume'},
                            convention='interval-end', volume_unit='hand', amount_unit='CNY')
