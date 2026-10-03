"""EastMoney 净值历史分页；整批校验成功后才发布。"""

import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from .eastmoney_nav_evidence import (
    NavRawEvidence, NavRawPage, decode_source_json, evidence_hash,
    hash_raw, native_record_fragments, parse_nav_record,
)

ENDPOINT = 'https://fundmobapi.eastmoney.com/FundMNewApi/FundMNHisNetList'
PAGE_SIZE = 1000
MAX_ROWS = 100000
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TOTAL_RESPONSE_BYTES = 32 * 1024 * 1024
READER_REVISION = 'eastmoney-fund-nav-raw-v1'


def read_fund_nav_evidence(fund_code: str) -> NavRawEvidence:
    if len(fund_code) != 6 or not fund_code.isascii() or not fund_code.isdigit():
        raise ValueError('基金代码无效')
    deadline = time.monotonic() + 55
    rows, dates, pages = [], set(), []
    total_size = 0
    expected_total = None
    for page in range(1, 101):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('基金净值读取预算耗尽')
        data = {
            'FCODE': fund_code, 'IsShareNet': 'true', 'MobileKey': '1', 'appType': 'ttjj',
            'appVersion': '6.2.8', 'cToken': '1', 'deviceid': '1', 'pageIndex': str(page),
            'pageSize': str(PAGE_SIZE), 'plat': 'Iphone', 'product': 'EFund',
            'serverVersion': '6.2.8', 'uToken': '1', 'userId': '1', 'version': '6.2.8',
        }
        with requests.get(ENDPOINT, data=data, headers={'User-Agent': 'Mozilla/5.0',
                          'Referer': 'https://fund.eastmoney.com/'},
                          timeout=(min(5, remaining), min(10, remaining)),
                          verify=True, stream=True) as response:
            response.raise_for_status()
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                total_size += len(chunk)
                if size > MAX_RESPONSE_BYTES or total_size > MAX_TOTAL_RESPONSE_BYTES or time.monotonic() >= deadline:
                    raise ValueError('基金净值响应超过读取预算')
                chunks.append(chunk)
            raw = b''.join(chunks).decode('utf-8')
            payload = decode_source_json(raw)
            pages.append(NavRawPage(page, raw, hash_raw(raw)))
        if not isinstance(payload, dict) or payload.get('Success') is not True:
            raise ValueError('基金净值来源未成功')
        if payload.get('ErrCode') != 0 or str(payload.get('ErrorCode')) != '0':
            raise ValueError('基金净值来源错误')
        total, items = payload.get('TotalCount'), payload.get('Datas')
        if type(total) is not int or not 0 <= total <= MAX_ROWS or not isinstance(items, list):
            raise ValueError('基金净值分页元数据无效')
        if expected_total is None:
            expected_total = total
        if total != expected_total or len(items) != min(PAGE_SIZE, total - len(rows)):
            raise ValueError('基金净值分页总量或页长度不一致')
        fragments = native_record_fragments(raw)
        if len(fragments) != len(items):
            raise ValueError('基金净值原文记录数量不符')
        for item, fragment in zip(items, fragments):
            record = parse_nav_record(item, page, fragment)
            if record.valuation_date in dates:
                raise ValueError('基金净值分页日期重复')
            dates.add(record.valuation_date)
            rows.append(record)
        if len(rows) == total:
            if time.monotonic() >= deadline:
                raise TimeoutError('基金净值读取预算耗尽')
            captured = datetime.now(timezone.utc)
            today = captured.astimezone(ZoneInfo('Asia/Shanghai')).date().isoformat()
            if any(record.valuation_date > today for record in rows):
                raise ValueError('基金净值包含晚于采集日的未来估值日期')
            return NavRawEvidence(
                fund_code, ENDPOINT, READER_REVISION, captured.isoformat(), total,
                tuple(pages), tuple(rows), evidence_hash(fund_code, ENDPOINT, READER_REVISION, pages),
            )
    raise ValueError('基金净值分页预算耗尽')


def read_fund_nav(fund_code: str) -> pd.DataFrame:
    """既有显示接口消费同一整批 Reader，继续返回原有四列。"""
    evidence = read_fund_nav_evidence(fund_code)
    rows = []
    for record in evidence.records:
        item = json.loads(record.native_record_raw)
        rows.append({'日期': record.valuation_date, '单位净值': record.unit_nav,
                     '累计净值': item.get('LJJZ'), '涨跌幅': item.get('JZZZL')})
    return pd.DataFrame(rows, columns=['日期', '单位净值', '累计净值', '涨跌幅'])
