"""市场资料适配层的原值转换与受控结果投影。"""

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
import time

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_reference_catalog_v1 import CATALOG, columns
from src.services.thesis_ledger_reference_contract_v1 import ReferenceResult

ADAPTER_REVISION = 'market-reference-projection-v1'


def number(value, integer=False):
    if value is None or value in ('', '-', '--'):
        return None
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        raise ValueError('来源数字类型不符')
    try:
        raw = str(value)
        if len(raw) > 128:
            raise ValueError('来源数字长度异常')
        numeric = Decimal(raw)
    except InvalidOperation:
        raise ValueError('来源数字无法解析') from None
    if not numeric.is_finite() or (integer and numeric != numeric.to_integral_value()):
        raise ValueError('来源数字不是有限值或整数')
    if abs(numeric.adjusted()) > 64 or len(numeric.as_tuple().digits) > 64:
        raise ValueError('来源数字超出资料表示边界')
    if integer:
        return str(int(numeric))
    return format(numeric, 'f')


def day(value):
    if value is None or value == '':
        return None
    if not isinstance(value, str):
        raise ValueError('来源日期类型不符')
    if re.fullmatch(r'\d{8}', value):
        return datetime.strptime(value, '%Y%m%d').date().isoformat()
    stamp = datetime.fromisoformat(value)
    return stamp.date().isoformat()


def text(value):
    if value is None or value == '':
        return None
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise ValueError('来源文本字段无效')
    return str(value)


def identity(row, query, field='SECURITY_CODE'):
    if query.symbol is None or str(row.get(field)) != query.symbol[:6]:
        raise ValueError('来源证券身份不符')
    if row.get('SECUCODE') is not None and row['SECUCODE'] != query.symbol:
        raise ValueError('来源交易所身份不符')


def result(query, provider, upstream, source_revision, source_url, records, *, total=None, has_more=False, next_cursor=None, notes=()):
    values = []
    seen = set()
    for record in records:
        if set(record) != {col.key for col in CATALOG[query.dataset][2]}:
            raise ValueError('来源投影字段不完整')
        key = json.dumps([query.dataset, query.symbol, query.indexCode, record], ensure_ascii=False, sort_keys=True)
        identifier = sha256(key.encode()).hexdigest()
        if identifier not in seen:
            values.append({'id': identifier, 'values': record})
            seen.add(identifier)
    if len(values) > query.pageSize:
        raise ValueError('来源结果超过请求页大小')
    return ReferenceResult.model_validate({
        **query.model_dump(exclude={'requestBudgetMs'}),
        'providerId': provider, 'upstreamSource': upstream, 'adapterRevision': ADAPTER_REVISION,
        'sourceRevision': source_revision, 'fetchedAt': datetime.now(timezone.utc).isoformat(),
        'sourceUrl': source_url, 'columns': columns(query.dataset), 'items': values,
        'status': 'ok' if values else 'empty', 'hasMore': has_more, 'nextCursor': next_cursor,
        'totalSourceRecords': total, 'historyComplete': False,
        'notes': ['历史完整性未声明；抓取时间与披露时间分别记录。', *notes],
    })


def require(value, message):
    if not value:
        raise ValueError(message)


def http_bytes(url, params, headers, seconds, *, method='GET', data=None):
    import requests
    started = time.monotonic()
    try:
        require(method in {'GET', 'POST'}, '资料请求方法不符')
        call = requests.get if method == 'GET' else requests.post
        options = {'params': params, 'headers': headers, 'timeout': seconds,
                   'allow_redirects': False, 'stream': True, 'auth': lambda request: request}
        if method == 'POST':
            options['data'] = data
        with call(url, **options) as response:
            if response.status_code in (401, 403):
                raise CapabilityContractError('permission_denied', '来源权限限制')
            if response.status_code == 429:
                raise CapabilityContractError('rate_limited', '来源频率限制')
            response.raise_for_status()
            if 300 <= response.status_code < 400:
                raise ValueError('来源返回非预期跳转')
            chunks, size = [], 0
            for chunk in response.iter_content(chunk_size=65536):
                size += len(chunk)
                if size > 8_000_000:
                    raise ValueError('资料响应超过读取边界')
                if time.monotonic() - started > seconds:
                    raise CapabilityContractError('timeout', '资料来源读取预算耗尽')
                chunks.append(chunk)
            return b''.join(chunks)
    except requests.Timeout:
        raise CapabilityContractError('timeout', '资料来源读取超时') from None
    except requests.RequestException:
        raise CapabilityContractError('upstream_failure', '资料来源连接或响应失败') from None


def http_json(url, params, headers, seconds, *, encoding='utf-8'):
    try:
        return json.loads(http_bytes(url, params, headers, seconds).decode(encoding), parse_float=Decimal)
    except (UnicodeError, json.JSONDecodeError):
        raise CapabilityContractError('invalid_response', '资料来源返回无效 JSON') from None
