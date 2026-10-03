"""新浪 ETF 节点的当前完整目录；计数、身份或读取期限不符时整体拒绝。"""

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import re
import time

import requests

_ROOT = 'https://vip.stock.finance.sina.com.cn/quotes_service/api/'
_COUNT = _ROOT + 'json_v2.php/Market_Center.getHQNodeStockCount'
_CALLBACK = "IO.XSRV2.CallbackList['da_yPT46_Ll7K6WD']"
_PREFIX = "/*<script>location.href='//sina.com';</script>*/"
_LIST = _ROOT + 'jsonp.php/' + _CALLBACK + '/Market_Center.getHQNodeDataSimple'
_LIMIT = 5000


def _fetch(url, params, remaining):
    with requests.get(url, params=params, timeout=(min(5, remaining), remaining),
                      allow_redirects=False, stream=True) as response:
        if response.status_code != 200:
            raise ValueError('catalog_http_failure')
        chunks, size = [], 0
        for chunk in response.iter_content(16384):
            size += len(chunk)
            if size > 2 * 1024 * 1024:
                raise ValueError('catalog_response_too_large')
            chunks.append(chunk)
        content = b''.join(chunks)
        # 来源接口使用 GBK；UTF-8 响应仅在显式声明时采用，不猜测字符集。
        content_type = response.headers.get('Content-Type', '').lower()
        encoding = 'utf-8' if 'charset=utf-8' in content_type else 'gb18030'
        return content.decode(encoding), sha256(content).hexdigest()


def _count(text):
    value = json.loads(text)
    if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]*', value):
        value = int(value)
    if type(value) is not int or not 0 < value < _LIMIT:
        raise ValueError('invalid_etf_catalog_count')
    return value


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_etf_catalog_field')
        result[key] = value
    return result


def _rows(text, expected):
    text = text.strip()
    if text.startswith(_PREFIX):
        text = text[len(_PREFIX):].lstrip()
    match = re.fullmatch(re.escape(_CALLBACK) + r'\((\[.*\])\);?\s*', text.strip(), re.S)
    if match is None:
        raise ValueError('invalid_etf_catalog_envelope')
    values = json.loads(match[1], object_pairs_hook=_unique_fields)
    if not isinstance(values, list) or len(values) != expected:
        raise ValueError('incomplete_etf_catalog')
    rows, seen = [], set()
    for item in values:
        if not isinstance(item, dict):
            raise ValueError('invalid_etf_catalog_identity')
        symbol, code, name = item.get('symbol'), item.get('code'), item.get('name')
        if (not isinstance(symbol, str) or not re.fullmatch(r'(sh|sz)[0-9]{6}', symbol)
                or code != symbol[2:] or code == '000000'
                or not isinstance(name, str) or not name.strip() or symbol in seen):
            raise ValueError('invalid_etf_catalog_identity')
        seen.add(symbol)
        rows.append({'canonicalCode': code, 'market': symbol[:2].upper(),
                     'instrumentType': 'ETF', 'displayName': name.strip()})
    return rows


def read_sina_etf_catalog(*, timeout_seconds=20, fetch=_fetch, monotonic=time.monotonic):
    """单次读取同节点计数→全列表→计数；没有重试或来源回退。"""
    if (type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30 or not callable(fetch)):
        raise ValueError('invalid_catalog_budget')
    deadline = monotonic() + timeout_seconds
    hashes = []

    def request(url, params):
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError('catalog_deadline')
        text, digest = fetch(url, params, remaining)
        if monotonic() >= deadline:
            raise TimeoutError('catalog_deadline')
        hashes.append(digest)
        return text

    before = _count(request(_COUNT, {'node': 'etf_hq_fund'}))
    list_params = {'page': '1', 'num': str(_LIMIT), 'sort': 'symbol',
                   'asc': '0', 'node': 'etf_hq_fund'}
    rows = _rows(request(_LIST, list_params), before)
    after = _count(request(_COUNT, {'node': 'etf_hq_fund'}))
    if before != after:
        raise ValueError('catalog_changed_during_read')
    evidence = {'protocol': 'sina-etf-node-catalog-v1', 'node': 'etf_hq_fund',
                'countMethod': 'Market_Center.getHQNodeStockCount',
                'total': before, 'rowLimit': _LIMIT, 'responseHashes': hashes}
    digest = sha256(json.dumps(evidence, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'rows': rows, 'retrieval': evidence, 'contentFingerprint': digest,
            'observedAt': datetime.now(timezone.utc).isoformat()}
