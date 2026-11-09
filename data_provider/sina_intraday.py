"""新浪既有公开分钟上游，固定单证券、不复权和有界 JSONP 读取。"""

import json
import pandas as pd
import requests
from data_provider.intraday_projection import cn_identity, project_intraday
from src.services.thesis_ledger_capability_contracts import CapabilityContractError


def project_payload(payload, query):
    if not isinstance(payload, list) or len(payload) > 1970:
        raise CapabilityContractError('invalid_response', '新浪分钟响应不是有界数据列表')
    return project_intraday(pd.DataFrame(payload), query, 'sina', columns={'day': 'timestamp'},
        convention='source-native')


def read_minute(query, context):
    code, exchange = cn_identity(query)
    response = requests.get('https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData',
        params={'symbol': exchange.lower() + code, 'scale': query.timeframe[:-1], 'ma': 'no', 'datalen': '1970'},
        timeout=context.timeout_seconds, allow_redirects=False)
    response.raise_for_status()
    try:
        payload = json.loads(response.text.split('=(', 1)[1].rsplit(');', 1)[0])
    except (ValueError, IndexError):
        raise CapabilityContractError('invalid_response', '新浪分钟 JSONP 响应无效') from None
    return project_payload(payload, query)
