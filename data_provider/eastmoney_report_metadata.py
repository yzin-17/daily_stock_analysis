"""东财研报列表单页适配；只投影最小元数据，不读取附件和正文。"""

from datetime import datetime, timezone
import json
import logging
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_report_metadata_contract import (
    CONTRACT, ReportMetadataItem, ReportMetadataResult,
)
from src.services.thesis_ledger_capability_registry import ExactKey, QueryBinding

ENDPOINT = 'https://reportapi.eastmoney.com/report/list'
MAX_RESPONSE_BYTES = 256 * 1024
ADAPTER_REVISION = 'eastmoney-report-metadata-page-v2'
SOURCE_REVISION = 'eastmoney-report-list-single-page-v2'
logger = logging.getLogger(__name__)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, _request, _file, _code, _message, _headers, _url):
        return None


def open_list(request, *, timeout):
    return build_opener(_NoRedirect()).open(request, timeout=timeout)


def project_page(payload, query, fetched_at):
    try:
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ValueError('列表封套无效')
        for field in ('hits', 'size', 'TotalPage', 'pageNo'):
            if type(payload.get(field)) is not int:
                raise ValueError('分页字段无效')
        if payload['pageNo'] != query.page or payload['size'] != query.pageSize:
            raise ValueError('来源返回了其他页')
        items = []
        for row in payload['data']:
            if not isinstance(row, dict) or row.get('stockCode') != query.symbol[:6]:
                raise ValueError('研报证券身份不匹配')
            raw_date = row.get('publishDate')
            if not isinstance(raw_date, str) or len(raw_date) < 10:
                raise ValueError('研报发布日期缺失')
            reported = datetime.fromisoformat(raw_date)
            source_id = row.get('infoCode')
            if not isinstance(source_id, str) or not re.fullmatch(r'AP[0-9]{18}', source_id):
                raise ValueError('来源研报标识无效')
            items.append(ReportMetadataItem(
                sourceReportId=source_id, reference=f'eastmoney:report:{source_id}',
                title=row.get('title'), publisher=row.get('orgSName'),
                publishedDate=reported.date().isoformat(),
                originalUrl=f'https://pdf.dfcfw.com/pdf/H3_{source_id}_1.pdf',
            ))
        result = ReportMetadataResult(requestId=query.requestId, symbol=query.symbol,
            start=query.start, end=query.end, page=query.page, pageSize=query.pageSize,
            totalItems=payload['hits'], totalPages=payload['TotalPage'], fetchedAt=fetched_at,
            providerId='akshare', upstreamSource='eastmoney', items=tuple(items),
            adapterRevision=ADAPTER_REVISION, sourceRevision=SOURCE_REVISION)
        return CONTRACT.correlate_result(query, result, {'providerId': 'akshare', 'upstreamSource': 'eastmoney'})
    except (ValueError, TypeError, OverflowError):
        raise CapabilityContractError('invalid_response', '研报列表身份、日期、元数据或分页校验失败') from None


def read_page(query, context):
    budget = min(query.requestBudgetMs / 1000, context.timeout_seconds)
    if budget <= 0:
        raise CapabilityContractError('timeout', '研报请求预算已耗尽')
    params = {'industryCode': '*', 'pageSize': str(query.pageSize), 'industry': '*',
              'rating': '*', 'ratingChange': '*', 'beginTime': query.start or '', 'endTime': query.end or '',
              'pageNo': str(query.page), 'fields': '', 'qType': '0', 'orgCode': '',
              'code': query.symbol[:6], 'rcode': '', 'p': str(query.page),
              'pageNum': str(query.page), 'pageNumber': str(query.page)}
    request = Request(ENDPOINT + '?' + urlencode(params),
                      headers={'Referer': 'https://data.eastmoney.com/', 'User-Agent': 'Mozilla/5.0'})
    started = time.monotonic()
    try:
        with open_list(request, timeout=budget) as response:
            if response.status != 200:
                logger.warning('report-metadata requestId=%s code=upstream_failure httpStatus=%s',
                               query.requestId, response.status)
                raise CapabilityContractError('upstream_failure', '研报来源未返回成功响应')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if time.monotonic() - started > budget:
            raise CapabilityContractError('timeout', '研报来源返回时已超过请求预算')
        if len(raw) > MAX_RESPONSE_BYTES:
            raise CapabilityContractError('invalid_response', '研报响应超过大小上限')
        payload = json.loads(raw)
    except HTTPError as error:
        code = 'upstream_failure'
        if error.code in (401, 403):
            code = 'permission_denied'
        elif error.code == 429:
            code = 'rate_limited'
        logger.warning('report-metadata requestId=%s code=%s httpStatus=%s', query.requestId, code, error.code)
        raise CapabilityContractError(code, '研报来源请求未获成功响应') from None
    except (TimeoutError, socket.timeout):
        logger.warning('report-metadata requestId=%s code=timeout', query.requestId)
        raise CapabilityContractError('timeout', '研报来源请求超时') from None
    except URLError as error:
        code = 'timeout' if isinstance(error.reason, (TimeoutError, socket.timeout)) else 'upstream_failure'
        logger.warning('report-metadata requestId=%s code=%s errorType=%s',
                       query.requestId, code, type(error.reason).__name__)
        raise CapabilityContractError(code, '研报来源连接失败') from None
    except OSError as error:
        logger.warning('report-metadata requestId=%s code=upstream_failure errorType=%s',
                       query.requestId, type(error).__name__)
        raise CapabilityContractError('upstream_failure', '研报来源读取中断') from None
    except (ValueError, UnicodeError):
        raise CapabilityContractError('invalid_response', '研报来源响应不是有效 JSON') from None
    return project_page(payload, query, datetime.now(timezone.utc).isoformat())


BINDING = QueryBinding(CONTRACT, ExactKey('CN', 'STOCK', 'RESEARCH_REPORT_METADATA'), 'eastmoney',
    ADAPTER_REVISION, SOURCE_REVISION, read_page,
    admission_required=False)
