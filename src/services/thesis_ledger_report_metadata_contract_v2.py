"""研报长期按需合同；日期是可选筛选，旧版结构继续独立保留。"""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_report_metadata_contract_v1 import (
    CAPABILITY, MetadataModel, ReportMetadataItem, ReportMetadataResult as LegacyResult, validate_binding,
)


class ReportMetadataRequest(MetadataModel):
    symbol: str = Field(pattern=r'^[0-9]{6}\.(SH|SZ)$')
    instrument_type: Literal['STOCK'] = 'STOCK'
    requestId: str = Field(min_length=1, max_length=128)
    start: str | None = None
    end: str | None = None
    page: int = Field(ge=1, le=1000)
    pageSize: int = Field(ge=1, le=20)
    requestBudgetMs: int = Field(ge=500, le=5000)

    @model_validator(mode='after')
    def filters(self):
        for value in (self.start, self.end):
            if value is not None and date.fromisoformat(value).isoformat() != value:
                raise ValueError('研报筛选需要 ISO 日期')
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError('研报日期筛选逆序')
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


class ReportMetadataResult(LegacyResult):
    metadataVersion: Literal['research-report-metadata-v2'] = 'research-report-metadata-v2'
    start: str | None = None
    end: str | None = None


def correlate(request, result, target):
    for field in ('requestId', 'symbol', 'start', 'end', 'page', 'pageSize'):
        if getattr(request, field) != getattr(result, field):
            raise ValueError('研报结果与请求不一致')
    if (result.providerId, result.upstreamSource) != (target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('研报结果与来源目标不一致')
    for item in result.items:
        if ((request.start is not None and item.publishedDate < request.start)
                or (request.end is not None and item.publishedDate > request.end)):
            raise ValueError('研报日期超出可选筛选')
    return result


CONTRACT = CapabilityContract(CAPABILITY, ReportMetadataRequest, ReportMetadataResult.model_validate,
                              validate_binding=validate_binding, correlate_result=correlate)
