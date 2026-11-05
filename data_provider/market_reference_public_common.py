"""公开资料的单次读取预算、来源关联及分页投影。"""

from decimal import Decimal
import json
import time
from urllib.parse import urljoin, urlparse

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_reference_catalog_v1 import DATE_FILTERS
from .market_reference_projection import http_bytes, require, result
from .market_reference_public_sources import SOURCES


class PublicRead:
    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds

    def read(self, url, params=None, headers=None, *, method='GET', data=None):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise CapabilityContractError('timeout', '公开资料读取预算耗尽')
        return http_bytes(url, params or {}, {'User-Agent': 'Mozilla/5.0', **(headers or {})},
                          remaining, method=method, data=data)

    def json(self, url, params=None, headers=None, *, method='GET', data=None):
        return json.loads(self.read(url, params, headers, method=method, data=data), parse_float=Decimal)


def source_result(query, records, source_url, *, total=None, has_more=False, notes=(), source=None):
    provider, source, revision = source or SOURCES[query.dataset]
    return result(query, provider, source, revision, source_url, records,
                  total=total, has_more=has_more, notes=notes)


def paged(query, records, source_url, *, notes=(), source=None):
    field = DATE_FILTERS.get(query.dataset)
    if field:
        records = [row for row in records if (not query.start or row[field] >= query.start)
                   and (not query.end or row[field] <= query.end)]
    start, stop = (query.page - 1) * query.pageSize, query.page * query.pageSize
    return source_result(query, records[start:stop], source_url,
                         total=len(records), has_more=stop < len(records), notes=notes, source=source)


def same_publisher_link(base, href, host):
    target = urljoin(base, href)
    parsed = urlparse(target)
    require(parsed.scheme == 'https' and parsed.hostname == host and not parsed.username and not parsed.password,
            '来源链接跳到未核对的发布机构')
    return target


def unique(records, keys):
    identities = [tuple(row[key] for key in keys) for row in records]
    require(len(identities) == len(set(identities)), '来源记录身份重复')


def normalized_code(value):
    import re
    raw = str(value).strip()
    require(bool(re.fullmatch(r'\d{1,6}', raw)), '来源代码无效')
    return raw.zfill(6)
