"""东方财富当前基金身份字段；类型核验不使用名称或历史交易状态。"""

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import time

import requests
from akshare.fund.fund_em import demjson, headers

from data_provider.eastmoney_nav_evidence import hash_raw


IDENTITY_ENDPOINT = 'https://fund.eastmoney.com/Data/Fund_JJJZ_Data.aspx'
IDENTITY_REVISION = 'eastmoney-fund-identity-v1'


@dataclass(frozen=True)
class FundIdentityEvidence:
    fund_code: str
    fund_type: str
    source_type: str
    response_raw: str
    captured_at: str

    @property
    def record(self):
        return {'symbol': f'{self.fund_code}.OF', 'fundType': self.fund_type,
                'sourceType': self.source_type, 'endpoint': IDENTITY_ENDPOINT,
                'readerRevision': IDENTITY_REVISION, 'responseRaw': self.response_raw,
                'responseHash': hash_raw(self.response_raw), 'capturedAt': self.captured_at}


def identity_from_raw(raw: str, fund_code: str, captured_at: str) -> FundIdentityEvidence:
    if not re.fullmatch(r'[0-9]{6}', fund_code) or len(raw.encode('utf-8')) > 8 * 1024 * 1024:
        raise ValueError('基金身份代码或原文预算无效')
    match = re.fullmatch(r'\s*var\s+reData\s*=\s*(.*?)\s*;?\s*', raw, re.S)
    if not match:
        raise ValueError('基金身份来源信封无效')
    payload = demjson.decode(match[1])
    rows = payload.get('datas') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not 0 < len(rows) <= 100000:
        raise ValueError('基金身份来源列表无效')
    matches = [row for row in rows if isinstance(row, list) and row and row[0] == fund_code]
    if len(matches) != 1 or len(matches[0]) < 3 or not isinstance(matches[0][2], str):
        raise ValueError('基金身份来源缺失或重复')
    source_type = matches[0][2]
    if re.fullmatch(r'QDII(?:-[^\s]+)?', source_type):
        fund_type = 'qdii'
    elif (re.fullmatch(r'(?:股票型|混合型|债券型|货币型|理财型|FOF)(?:-[^\s]+)?', source_type)
          or source_type in {'指数型', '指数型-股票', '指数型-固收', '指数型-其他'}):
        fund_type = 'domestic'
    else:
        raise ValueError('基金来源类型尚未经分类核验')
    return FundIdentityEvidence(fund_code, fund_type, source_type, raw, captured_at)


def read_fund_identity(fund_code: str) -> FundIdentityEvidence:
    if not isinstance(fund_code, str) or not re.fullmatch(r'[0-9]{6}', fund_code):
        raise ValueError('基金身份代码无效')
    deadline = time.monotonic() + 30
    chunks, size = [], 0
    with requests.get(IDENTITY_ENDPOINT, params={'t': '8', 'page': '1,50000', 'js': 'reData',
                      'sort': 'fcode,asc'}, headers=headers, timeout=(5, 10), verify=True,
                      stream=True, allow_redirects=False) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('基金身份来源响应状态无效')
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > 8 * 1024 * 1024 or time.monotonic() >= deadline:
                raise ValueError('基金身份来源读取预算耗尽')
            chunks.append(chunk)
    raw = b''.join(chunks).decode('utf-8', errors='strict')
    evidence = identity_from_raw(raw, fund_code, datetime.now(timezone.utc).isoformat())
    if time.monotonic() >= deadline:
        raise TimeoutError('基金身份来源读取预算耗尽')
    return evidence
