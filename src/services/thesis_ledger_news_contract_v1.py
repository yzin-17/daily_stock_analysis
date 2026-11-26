"""news-search-v1：当前新闻取数合同；不宣称历史覆盖或模型证据资格。"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .thesis_ledger_news_identity import (
    news_evidence_group, news_item_id, news_time, news_url, validate_news_symbol,
)

NEWS_CAPABILITY = "NEWS_SEARCH"
NEWS_CONTRACT_VERSION = "news-search-v1"
NewsErrorCode = Literal[
    "not_admitted", "missing_credentials", "configuration_failure", "rate_limited",
    "timeout", "upstream_failure", "ambiguous_instrument", "invalid_request",
    "not_covered", "unsupported",
]


class NewsModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class NewsInstrument(NewsModel):
    market: Literal["CN", "HK", "US"]
    assetType: Literal["STOCK", "ETF", "INDEX", "MUTUAL_FUND"]
    symbol: str = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def canonical_identity(self):
        validate_news_symbol(self.market, self.assetType, self.symbol)
        return self


class NewsWindowRequest(NewsModel):
    version: Literal[1] = 1
    requestId: str = Field(min_length=1, max_length=128)
    publishedStart: str | None = None
    publishedEnd: str | None = None
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    requestBudgetMs: int = Field(default=30_000, ge=1, le=30_000)

    @model_validator(mode="after")
    def ordered_window(self):
        start = news_time(self.publishedStart) if self.publishedStart is not None else None
        end = news_time(self.publishedEnd) if self.publishedEnd is not None else None
        if start and end and start > end:
            raise ValueError("新闻发布时间窗口必须有序")
        return self


class NewsRequest(NewsWindowRequest):
    contractVersion: Literal["news-search-v1"] = NEWS_CONTRACT_VERSION
    capability: Literal["NEWS_SEARCH"] = NEWS_CAPABILITY
    instrument: NewsInstrument


class NewsRoute(NewsModel):
    providerId: str = Field(min_length=1, max_length=128)
    upstreamSource: str = Field(min_length=1, max_length=128)
    policyRevision: str = Field(min_length=1, max_length=200)
    routeRevision: str = Field(min_length=1, max_length=200)
    credentialRevision: str | None = Field(default=None, min_length=1, max_length=200)


class NewsItem(NewsModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=1000)
    url: str = Field(min_length=1, max_length=4096)
    originalUrl: str | None = Field(default=None, max_length=4096)
    publisher: str | None = Field(default=None, min_length=1, max_length=256)
    providerId: str = Field(min_length=1, max_length=128)
    sourceItemId: str | None = Field(default=None, min_length=1, max_length=256)
    contentFingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidenceGroupId: str = Field(min_length=1, max_length=100)
    excerpt: str | None = Field(default=None, max_length=20_000)
    summaryKind: Literal["provider-excerpt"] = "provider-excerpt"
    publishedAt: str | None
    sourceAvailableAt: str | None
    fetchedAt: str
    relatedInstruments: list[NewsInstrument] = Field(min_length=1)
    sourceUrls: list[str] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def evidence_identity(self):
        if not self.title.strip():
            raise ValueError("新闻标题不能只有空白")
        urls = {news_url(url) for url in self.sourceUrls}
        if news_url(self.url) not in urls or (self.originalUrl and news_url(self.originalUrl) not in urls):
            raise ValueError("新闻引用必须保留条目 URL 与已知原始出处")
        if self.id != news_item_id(self.providerId, self.sourceItemId, self.url):
            raise ValueError("新闻条目身份与实际来源不一致")
        if self.evidenceGroupId != news_evidence_group(self.originalUrl, self.contentFingerprint):
            raise ValueError("新闻独立证据分组与原始出处或内容指纹不一致")
        fetched = news_time(self.fetchedAt)
        for value in (self.publishedAt, self.sourceAvailableAt):
            if value is not None and news_time(value) > fetched:
                raise ValueError("新闻来源时间不能晚于本次抓取时间")
        return self


class NewsError(NewsModel):
    code: NewsErrorCode
    message: str = Field(min_length=1, max_length=512)
    providerId: str | None = None
    upstreamSource: str | None = None


class NewsCoverage(NewsModel):
    basis: Literal["current-search"] = "current-search"
    publishedStart: str | None = None
    publishedEnd: str | None = None
    complete: bool
    truncated: bool
    unknownPublishedTimeCount: int = Field(ge=0)
    examinedCount: int | None = Field(default=None, ge=0)
    returnedCount: int = Field(ge=0)
    uniqueEvidenceCount: int = Field(ge=0)
    missingReasons: list[str] = Field(max_length=50)

    @field_validator("missingReasons")
    @classmethod
    def meaningful_reasons(cls, values):
        if any(not value.strip() for value in values):
            raise ValueError("新闻缺失原因不能为空")
        return values

    @model_validator(mode="after")
    def ordered_window(self):
        if self.publishedStart and self.publishedEnd and news_time(self.publishedStart) > news_time(self.publishedEnd):
            raise ValueError("新闻覆盖窗口必须有序")
        return self

    @field_validator("publishedStart", "publishedEnd")
    @classmethod
    def explicit_time(cls, value):
        if value is not None:
            news_time(value)
        return value


class NewsResponseContent(NewsModel):
    requestId: str = Field(min_length=1, max_length=128)
    status: Literal["available", "partial", "unavailable"]
    items: list[NewsItem] = Field(max_length=50)
    fetchedAt: str
    route: NewsRoute | None
    upstreamAttempts: int = Field(ge=0)
    nextCursor: str | None = Field(default=None, min_length=1, max_length=2048)
    coverage: NewsCoverage
    errors: list[NewsError] = Field(max_length=50)

    @model_validator(mode="after")
    def truthful_result(self):
        fetched = news_time(self.fetchedAt)
        coverage = self.coverage
        unknown = sum(item.publishedAt is None for item in self.items)
        groups = len({item.evidenceGroupId for item in self.items})
        if (coverage.returnedCount, coverage.unknownPublishedTimeCount, coverage.uniqueEvidenceCount) != (len(self.items), unknown, groups):
            raise ValueError("新闻条数、未知时间与独立证据计数不一致")
        if coverage.examinedCount is not None and coverage.examinedCount < len(self.items):
            raise ValueError("已检查条数不能小于返回条数")
        if len({news_url(item.url) for item in self.items}) != len(self.items) or len({item.id for item in self.items}) != len(self.items):
            raise ValueError("新闻响应必须先去除重复条目，转载仍共享独立证据分组")
        for item in self.items:
            if news_time(item.fetchedAt) > fetched:
                raise ValueError("新闻条目抓取时间与响应不一致")
            if self.route is None or item.providerId != self.route.providerId:
                raise ValueError("新闻条目必须绑定本次实际检索来源")
            self.validate_publication_window(item)
        self.validate_status()
        return self

    def validate_publication_window(self, item: NewsItem):
        if item.publishedAt is None:
            return
        published = news_time(item.publishedAt)
        start, end = self.coverage.publishedStart, self.coverage.publishedEnd
        if (start and published < news_time(start)) or (end and published > news_time(end)):
            raise ValueError("已知发布时间不能超出请求的闭区间")

    def validate_status(self):
        coverage = self.coverage
        if self.status == "unavailable":
            if self.items or not self.errors or coverage.complete:
                raise ValueError("不可用必须保留错误且不能携带可用条目或完整覆盖")
        elif self.route is None or self.upstreamAttempts == 0:
            raise ValueError("可用或部分结果必须来自实际成功执行的检索")
        if any(error.code == "not_admitted" for error in self.errors) and (self.upstreamAttempts != 0 or self.status != "unavailable"):
            raise ValueError("未准入新闻请求必须为零上游尝试")
        incomplete = bool(self.errors or coverage.truncated or coverage.unknownPublishedTimeCount or coverage.missingReasons)
        if self.status == "available" and (not coverage.complete or incomplete):
            raise ValueError("available 不能掩盖失败、未知时间或分页截断")
        if self.status == "partial" and (coverage.complete or not incomplete):
            raise ValueError("partial 必须说明真实的不完整原因")
        if self.nextCursor is not None and not coverage.truncated:
            raise ValueError("存在后续游标时必须披露截断")
        if coverage.truncated and "pagination_truncated" not in coverage.missingReasons:
            raise ValueError("分页截断必须保留明确缺失原因")
        if coverage.unknownPublishedTimeCount and "published_time_unknown" not in coverage.missingReasons:
            raise ValueError("未知发布时间必须保持未知覆盖")


class NewsResponse(NewsResponseContent):
    version: Literal[1] = 1
    contractVersion: Literal["news-search-v1"] = NEWS_CONTRACT_VERSION
    capability: Literal["NEWS_SEARCH"] = NEWS_CAPABILITY
    instrument: NewsInstrument

    @model_validator(mode="after")
    def instrument_binding(self):
        if any(self.instrument not in item.relatedInstruments for item in self.items):
            raise ValueError("新闻条目对象与响应不一致")
        return self
