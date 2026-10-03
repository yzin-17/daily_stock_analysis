"""基金排名目录的有界分页读取；完整结果只在所有页面校验后返回。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import time
from zoneinfo import ZoneInfo

import requests

from .eastmoney_fund_catalog_page import parse_fund_catalog_page


def _ranking_window():
    end = datetime.now(ZoneInfo('Asia/Shanghai')).date()
    return ((end - timedelta(days=365)).isoformat(), end.isoformat())


def _fetch_page(page, page_size, remaining, *, ranking_window=None):
    start, end = ranking_window or _ranking_window()
    params = {'op': 'ph', 'dt': 'kf', 'ft': 'all', 'rs': '', 'gs': '0', 'sc': 'qjzf',
              'st': 'desc', 'sd': start, 'ed': end, 'qdii': '', 'tabSubtype': ',,,,,',
              'pi': str(page), 'pn': str(page_size), 'dx': '1'}
    with requests.get('https://fund.eastmoney.com/data/rankhandler.aspx', params=params,
                      headers={'Referer': 'https://fund.eastmoney.com/data/fundranking.html'},
                      timeout=(min(5, remaining), remaining), allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise ValueError('catalog_http_failure')
        chunks, size = [], 0
        for chunk in response.iter_content(16384):
            size += len(chunk)
            if size > 8 * 1024 * 1024:
                raise ValueError('catalog_response_too_large')
            chunks.append(chunk)
        return b''.join(chunks).decode('utf-8-sig')


def read_fund_catalog(*, page_size=1000, max_pages=50, timeout_seconds=45,
                      fetch_page=None, monotonic=time.monotonic):
    """传输超时与阶段总预算并用；生产 Catalog 外层仍须提供进程硬期限。"""
    if (type(page_size) is not int or not 1 <= page_size <= 1000
            or type(max_pages) is not int or not 1 <= max_pages <= 100
            or type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 60
            or (fetch_page is not None and not callable(fetch_page))):
        raise ValueError('invalid_catalog_budget')
    ranking_window = _ranking_window()
    if fetch_page is None:
        def fetch_page(page, size, remaining):
            return _fetch_page(page, size, remaining, ranking_window=ranking_window)
    deadline = monotonic() + timeout_seconds
    rows, seen, hashes = [], set(), []
    total, pages, index = None, None, 1
    while pages is None or index <= pages:
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError('catalog_deadline')
        text = fetch_page(index, page_size, remaining)
        if monotonic() >= deadline:
            raise TimeoutError('catalog_deadline')
        page = parse_fund_catalog_page(text, page=index, page_size=page_size)
        if pages is None:
            total, pages = page.total, page.pages
            if pages > max_pages:
                raise ValueError('catalog_page_budget_exceeded')
        elif (page.total, page.pages) != (total, pages):
            raise ValueError('catalog_changed_during_read')
        for code, name in page.rows:
            if code in seen:
                raise ValueError('duplicate_catalog_identity')
            seen.add(code)
            rows.append((code, name))
        hashes.append(sha256(text.encode('utf-8')).hexdigest())
        index += 1
    if len(rows) != total or monotonic() >= deadline:
        raise ValueError('incomplete_catalog_read')
    evidence = {'protocol': 'eastmoney-rankhandler-pages-v2', 'pageSize': page_size,
                'operation': 'ph', 'fundType': 'all', 'rankingStart': ranking_window[0],
                'rankingEnd': ranking_window[1],
                'total': total, 'pages': pages, 'pageHashes': hashes}
    fingerprint = sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'rows': tuple(rows), 'retrieval': evidence, 'contentFingerprint': fingerprint,
            'observedAt': datetime.now(timezone.utc).isoformat()}
