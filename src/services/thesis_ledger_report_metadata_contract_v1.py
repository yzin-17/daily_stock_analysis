"""单标研报最小元数据合同；日期精度与局部读取边界显式保留。"""

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator
from .thesis_ledger_capability_registry import CapabilityContract

CAPABILITY = 'RESEARCH_REPORT_METADATA'


class MetadataModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')


class ReportMetadataRequest(MetadataModel):
    symbol: str = Field(pattern=r'^[0-9]{6}\.(SH|SZ)$')
    instrument_type: Literal['STOCK'] = 'STOCK'
    requestId: str = Field(min_length=1, max_length=128)
    start: str
    end: str
    page: int = Field(ge=1, le=1000)
    pageSize: int = Field(ge=1, le=20)
    requestBudgetMs: int = Field(ge=500, le=5000)

    @model_validator(mode='after')
    def window(self):
        for value in (self.start, self.end):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError('研报窗口需要 ISO 日期')
        if self.start > self.end:
            raise ValueError('研报日期窗口逆序')
        return self

    def route_key(self):
        return {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': CAPABILITY}

    @classmethod
    def from_request(cls, request):
        if (set(request.parameters) != {'page', 'pageSize', 'requestBudgetMs'}
                or request.timeframe is not None or request.adjustment is not None or request.limit is not None):
            raise ValueError('研报元数据不接受额外参数、周期或复权')
        return cls(symbol=request.symbol, instrument_type=request.instrument_type,
                   requestId=request.request_id, start=request.start, end=request.end, **request.parameters)


class ReportMetadataWireRequest(ReportMetadataRequest):
    instrument_type: Literal['STOCK'] = Field(default='STOCK', alias='assetType')
    contractVersion: Literal[3]
    consumer: Literal['thesis-ledger']


class ReportMetadataItem(MetadataModel):
    sourceReportId: str = Field(min_length=1, max_length=128)
    reference: str = Field(min_length=1, max_length=384)
    title: str = Field(min_length=1, max_length=400)
    publisher: str = Field(min_length=1, max_length=160)
    publishedDate: str
    datePrecision: Literal['date'] = 'date'
    originalUrl: str

    @model_validator(mode='after')
    def provenance(self):
        if date.fromisoformat(self.publishedDate).isoformat() != self.publishedDate:
            raise ValueError('研报发布日期需要 ISO 日期')
        link = urlsplit(self.originalUrl)
        if (link.scheme != 'https' or not link.hostname or link.username is not None
                or link.password is not None or not self.title.strip() or not self.publisher.strip()):
            raise ValueError('研报链接或最小元数据无效')
        return self


class ReportMetadataResult(MetadataModel):
    capability: Literal['RESEARCH_REPORT_METADATA'] = CAPABILITY
    metadataVersion: Literal['research-report-metadata-v1'] = 'research-report-metadata-v1'
    adapterRevision: str = Field(min_length=1)
    sourceRevision: str = Field(min_length=1)
    requestId: str
    symbol: str = Field(pattern=r'^[0-9]{6}\.(SH|SZ)$')
    start: str
    end: str
    page: int = Field(ge=1, le=1000)
    pageSize: int = Field(ge=1, le=20)
    totalItems: int = Field(ge=0)
    totalPages: int = Field(ge=0)
    historyComplete: Literal[False] = False
    fetchedAt: str
    providerId: str = Field(min_length=1)
    upstreamSource: str = Field(min_length=1)
    items: tuple[ReportMetadataItem, ...]

    @model_validator(mode='after')
    def page_integrity(self):
        if datetime.fromisoformat(self.fetchedAt).utcoffset() is None:
            raise ValueError('研报采集时间需要时区')
        expected_pages = (self.totalItems + self.pageSize - 1) // self.pageSize
        if self.totalPages not in ({0, 1} if self.totalItems == 0 else {expected_pages}):
            raise ValueError('研报来源分页总数不一致')
        expected_rows = max(0, min(self.pageSize, self.totalItems - (self.page - 1) * self.pageSize))
        if len(self.items) != expected_rows:
            raise ValueError('研报来源页内条数不完整')
        if len({item.sourceReportId for item in self.items}) != len(self.items):
            raise ValueError('研报页内来源标识重复')
        if any(item.reference != f'{self.upstreamSource}:report:{item.sourceReportId}' for item in self.items):
            raise ValueError('研报引用与来源命名空间不一致')
        return self


def validate_binding(request, key, _target):
    if request.route_key() != dict(key):
        raise ValueError('研报请求与精确能力键不一致')


def correlate(request, result, target):
    for field in ('requestId', 'symbol', 'start', 'end', 'page', 'pageSize'):
        if getattr(request, field) != getattr(result, field):
            raise ValueError('研报结果与请求不一致')
    if (result.providerId, result.upstreamSource) != (target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('研报结果与来源目标不一致')
    if any(not request.start <= item.publishedDate <= request.end for item in result.items):
        raise ValueError('研报日期超出请求窗口')
    return result


CONTRACT = CapabilityContract(CAPABILITY, ReportMetadataRequest, ReportMetadataResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
