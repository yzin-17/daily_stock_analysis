"""旧聚合状态窗口、证据角色和覆盖一致性；不改原冻结/消费格式。"""

from datetime import date, datetime, timedelta
from typing import Literal
from pydantic import Field, model_validator
from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_legacy_status_values_v1 import Model, Text, Key, Target, Window, StatusDay, Document, Record, Coverage, Lifecycle, Provenance

CAPABILITY = 'HISTORICAL_TRADING_STATUS'


class StatusRequest(Model):
    contractVersion: Literal[3]
    consumer: Literal['thesis-ledger']
    requestId: Text
    routeKey: Key
    routeTarget: Target
    symbol: Text
    range: Window

    @model_validator(mode='after')
    def consumer_identity(self):
        if self.consumer != 'thesis-ledger':
            raise ValueError('状态consumer不匹配')
        return self

    @classmethod
    def from_request(cls, request):
        if request.instrument_type != 'STOCK' or any(getattr(request, name) is not None for name in ('limit', 'timeframe', 'adjustment')):
            raise ValueError('旧聚合状态仅接受股票日期窗口')
        query = cls.model_validate({**dict(request.parameters or {}), 'symbol':request.symbol})
        if query.requestId != request.request_id or query.range.start != request.start or query.range.end != request.end:
            raise ValueError('状态参数与外层请求不一致')
        return query


class StatusResult(StatusRequest):
    status: Literal['supported']
    days: list[StatusDay] = Field(min_length=1)
    sourceRecords: list[Record] | None = Field(default=None, min_length=1)
    structuredCoverage: Coverage | None = None
    basicLifecycle: Lifecycle | None = None
    sourceDocuments: list[Document]
    provenance: Provenance

    @model_validator(mode='after')
    def evidence(self):
        if self.status != 'supported' or (self.provenance.providerId, self.provenance.upstreamSource) != (self.routeTarget.providerId, self.routeTarget.upstreamSource):
            raise ValueError('状态结果或来源不匹配')
        documents = {item.id for item in self.sourceDocuments}
        records = {item.id:item for item in self.sourceRecords or []}
        if len(documents) != len(self.sourceDocuments) or len(records) != len(self.sourceRecords or []):
            raise ValueError('状态来源引用重复')
        cursor = date.fromisoformat(self.range.start)
        for item in self.days:
            if item.date != cursor.isoformat() or item.date > self.range.end:
                raise ValueError('状态未逐日覆盖窗口')
            if not set(item.sourceDocumentIds or []).issubset(documents) or not set(item.sourceRecordIds or []).issubset(records):
                raise ValueError('状态引用缺少实际来源')
            if len(item.sourceRecordIds or []) != len(set(item.sourceRecordIds or [])):
                raise ValueError('结构化日引用重复')
            cursor += timedelta(days=1)
        if self.days[-1].date != self.range.end:
            raise ValueError('状态未覆盖终点')
        if any(item.intradayHalts for item in self.days):
            expected = {'CN':'Asia/Shanghai', 'HK':'Asia/Hong_Kong', 'US':'America/New_York'}[self.routeKey.market]
            if self.provenance.granularity != 'intraday' or self.provenance.timezone != expected:
                raise ValueError('盘中状态粒度或时区不匹配')
        for record in records.values():
            if (record.providerId, record.upstreamSource, record.symbol, record.range) != (self.provenance.providerId, self.provenance.upstreamSource, self.symbol, self.range):
                raise ValueError('结构化身份或窗口不匹配')
            if datetime.fromisoformat(record.observedAt) > datetime.fromisoformat(self.provenance.fetchedAt):
                raise ValueError('结构化观察晚于实际获取')
        self.validate_coverage(records)
        life = self.basicLifecycle
        if life is not None:
            if not records:
                raise ValueError('基础日期须附实际结构化来源')
            if life.sourceRecordId:
                if life.sourceRecordId not in records or any(life.sourceRecordId in (item.sourceRecordIds or []) for item in self.days):
                    raise ValueError('基础日期不能证明日级状态或粗ST')
                observed = datetime.fromisoformat(records[life.sourceRecordId].observedAt)
                if any(value and datetime.fromisoformat(value + 'T00:00:00+08:00') > observed for value in (life.ipoDate, life.outDate)):
                    raise ValueError('基础日期晚于实际观察')
        return self

    def validate_coverage(self, records):
        coverage = self.structuredCoverage
        if (coverage is not None) != bool(records):
            raise ValueError('结构化覆盖与来源不配对')
        if coverage is None:
            return
        for dates in (coverage.expectedSessions, coverage.calendarUnknownDates, coverage.statusUnknownDates, coverage.stUnknownDates):
            if dates != sorted(set(dates)) or any(not self.range.start <= value <= self.range.end for value in dates):
                raise ValueError('结构化覆盖日期非法或乱序')
        expected = set(coverage.expectedSessions)
        if expected.intersection(coverage.calendarUnknownDates) or any(item.sourceRecordIds and item.date not in expected for item in self.days):
            raise ValueError('结构化事实未对应已核实交易日')
        by_date = {item.date:item for item in self.days}
        missing = [value for value in coverage.expectedSessions if by_date[value].state == 'unknown']
        st_missing = [value for value in coverage.expectedSessions if by_date[value].coarseST is None]
        if missing != coverage.statusUnknownDates or st_missing != coverage.stUnknownDates or coverage.complete != (not coverage.calendarUnknownDates and not missing):
            raise ValueError('结构化覆盖与逐日未知状态不一致')


def binding(request, key, target):
    if request.routeKey.model_dump() != dict(key) or (request.routeTarget.providerId, request.routeTarget.upstreamSource) != (target.get('providerId'), target.get('upstreamSource')):
        raise ValueError('旧聚合状态请求与绑定不一致')


def correlate(request, result, target):
    for name in StatusRequest.model_fields:
        if getattr(result, name) != getattr(request, name):
            raise ValueError('状态结果与请求身份/窗口不一致')
    return result


CONTRACT = CapabilityContract(CAPABILITY, StatusRequest, StatusResult.model_validate,
                              validate_binding=binding, correlate_result=correlate)
