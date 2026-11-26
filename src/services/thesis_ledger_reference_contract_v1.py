"""市场资料单次按需读取的严格合同与关联约束。"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .thesis_ledger_reference_catalog_v1 import CATALOG, CAPABILITIES, DATE_FILTERS, capability, columns
from .thesis_ledger_capability_registry import CapabilityContract
from .thesis_ledger_reference_public_catalog_v1 import INDEX_CODES, SINGLE_DAY_DATASETS, SYMBOL_MARKETS
from .thesis_ledger_reference_derivative_catalog_v1 import DERIVATIVE_SINGLE_DAY
from .thesis_ledger_reference_scope_v1 import asset_type, valid_symbol, SYMBOL_SCOPES

VERSION = 'market-reference-v1'


class ReferenceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    requestId: str = Field(min_length=1, max_length=128)
    dataset: str
    symbol: str | None = None
    indexCode: str | None = None
    start: str | None = None
    end: str | None = None
    page: int = Field(default=1, ge=1, le=1000)
    pageSize: int = Field(default=5, ge=1, le=20)
    cursor: str | None = Field(default=None, max_length=128)
    requestBudgetMs: int = Field(default=60000, ge=1000, le=120000)

    @model_validator(mode='after')
    def validate_scope(self):
        if not self.requestId.strip():
            raise ValueError('请求标识不能为空白')
        if self.dataset not in CATALOG:
            raise ValueError('未知资料类型')
        if not valid_symbol(self.dataset, self.symbol):
            raise ValueError('资料代码格式或交易所范围不符')
        if CATALOG[self.dataset][1] == 'index':
            if self.indexCode != INDEX_CODES.get(self.dataset):
                raise ValueError('指数身份不属于本资料已核对的范围')
        elif self.indexCode is not None:
            raise ValueError('本资料不接受指数身份')
        expected_market = SYMBOL_MARKETS.get(self.dataset)
        if expected_market and (self.symbol is None or not self.symbol.endswith('.' + expected_market)):
            raise ValueError('本资料与证券交易所范围不符')
        if (self.start or self.end) and self.dataset not in DATE_FILTERS:
            raise ValueError('本资料仅支持来源当前页，不接受历史日期筛选')
        for value in [self.start, self.end]:
            if value is not None and date.fromisoformat(value).isoformat() != value:
                raise ValueError('日期不规范')
        if self.start and self.end and self.start > self.end:
            raise ValueError('日期倒序')
        if self.dataset == 'global-news':
            if (self.page == 1) != (self.cursor is None) or (self.cursor is not None and not re.fullmatch(r'[0-9A-Za-z_.:-]+', self.cursor)):
                raise ValueError('快讯分页需要来源游标')
        elif self.cursor is not None:
            raise ValueError('本资料不接受游标')
        if self.dataset == 'macro-calendar':
            if not self.start or not self.end or (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days > 6:
                raise ValueError('宏观日历需要最多七天的明确窗口')
        if self.dataset in (*SINGLE_DAY_DATASETS, *DERIVATIVE_SINGLE_DAY) and (not self.start or self.start != self.end):
            raise ValueError('本快照资料需要明确单个日期')
        if self.dataset == 'chinabond-curves':
            if not self.start or not self.end or (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days > 359:
                raise ValueError('中债曲线需要最多 360 天的明确窗口')
        if self.dataset == 'valuation-history':
            if self.start is None or self.end is None or (date.fromisoformat(self.end) - date.fromisoformat(self.start)).days > 3660:
                raise ValueError('估值历史需要明确且有界的日期窗口')
        return self

    def route_key(self):
        return {'kind': 'data', 'market': 'CN', 'assetType': asset_type(self.dataset), 'capability': capability(self.dataset)}

    @classmethod
    def from_request(cls, request):
        dataset = CAPABILITIES.get(request.capability)
        if dataset is None:
            raise ValueError('未知资料能力')
        return cls(requestId=request.request_id, dataset=dataset,
                   symbol=request.symbol if CATALOG[dataset][1] in SYMBOL_SCOPES else None,
                   start=request.start, end=request.end, **(request.parameters or {}))


class ReferenceWireRequest(ReferenceRequest):
    contractVersion: Literal[1]
    consumer: Literal['thesis-ledger']


class ReferenceColumn(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    key: str
    label: str
    format: Literal['text', 'decimal', 'integer', 'date', 'datetime', 'url']
    unit: str | None


class ReferenceRow(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[a-f0-9]{64}$')
    values: dict[str, str | None]


class ReferenceResult(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    dataVersion: Literal['market-reference-v1'] = VERSION
    requestId: str
    dataset: str
    symbol: str | None
    indexCode: str | None
    start: str | None
    end: str | None
    page: int
    pageSize: int
    cursor: str | None
    nextCursor: str | None = None
    providerId: str
    upstreamSource: str
    adapterRevision: str
    sourceRevision: str
    fetchedAt: str
    sourceUrl: str
    columns: list[ReferenceColumn]
    items: list[ReferenceRow]
    status: Literal['ok', 'empty']
    hasMore: bool
    totalSourceRecords: int | None
    historyComplete: Literal[False] = False
    notes: list[str]

    @model_validator(mode='after')
    def validate_values(self):
        if self.dataset not in CATALOG or [x.model_dump() for x in self.columns] != columns(self.dataset):
            raise ValueError('资料列定义不符')
        if self.status != ('ok' if self.items else 'empty') or len(self.items) > self.pageSize:
            raise ValueError('资料状态或行数无效')
        if len({row.id for row in self.items}) != len(self.items):
            raise ValueError('资料记录重复')
        if self.dataset == 'global-news' and (self.hasMore != (self.nextCursor is not None) or self.nextCursor == self.cursor and self.hasMore):
            raise ValueError('快讯分页游标不符')
        if self.dataset != 'global-news' and self.nextCursor is not None:
            raise ValueError('非快讯资料不能返回游标')
        if urlparse(self.sourceUrl).scheme != 'https' or urlparse(self.sourceUrl).username:
            raise ValueError('来源引用需要不含凭据的 HTTPS 地址')
        stamp = datetime.fromisoformat(self.fetchedAt)
        if stamp.tzinfo is None:
            raise ValueError('抓取时间缺时区')
        for row in self.items:
            if set(row.values) != {col.key for col in self.columns}:
                raise ValueError('记录字段与资料列不符')
            for col in self.columns:
                value = row.values[col.key]
                if value is None:
                    continue
                if len(value) > 8000:
                    raise ValueError('资料字段超出显示边界')
                if col.format in {'decimal', 'integer'}:
                    if not re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?' if col.format == 'decimal' else r'-?(?:0|[1-9]\d*)', value) or len(value) > 132:
                        raise ValueError('数字不是受控十进制表示')
                    try:
                        number = Decimal(value)
                    except InvalidOperation:
                        raise ValueError('数字无效') from None
                    if not number.is_finite() or (col.format == 'integer' and number != number.to_integral_value()):
                        raise ValueError('数字非有限值或整数不符')
                elif col.format == 'date':
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError('记录日期无效')
                elif col.format == 'datetime':
                    if datetime.fromisoformat(value).tzinfo is None:
                        raise ValueError('记录时间缺时区')
                elif col.format == 'url':
                    parsed = urlparse(value)
                    if parsed.scheme != 'https' or parsed.username or parsed.password:
                        raise ValueError('原文引用无效')
        return self


def correlate(request, result, target):
    for key in ('requestId', 'dataset', 'symbol', 'indexCode', 'start', 'end', 'page', 'pageSize', 'cursor'):
        if getattr(result, key) != getattr(request, key):
            raise ValueError('资料响应与请求身份不符')
    if (result.providerId, result.upstreamSource) != (target['providerId'], target['upstreamSource']):
        raise ValueError('资料来源与执行目标不符')
    return result


def validate_binding(request, key, _target):
    if key != request.route_key():
        raise ValueError('资料精确路由不符')


CONTRACTS = {name: CapabilityContract(name, ReferenceRequest, ReferenceResult.model_validate,
             validate_binding=validate_binding, correlate_result=correlate) for name in CAPABILITIES}
