"""只提供日期的基金披露证据；保守可用边界不冒充实际披露时刻。"""

import calendar
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal, Union
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

DIGEST = r'^[0-9a-f]{64}$'
CLOCK = r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}(?:Z|[+-]\d{2}:\d{2})$'


def publication_available_at(published_on: str, reference_observed_at: str) -> str:
    end = datetime.combine(date.fromisoformat(published_on) + timedelta(days=1), time(),
                           tzinfo=ZoneInfo('Asia/Shanghai'))
    observed = datetime.fromisoformat(reference_observed_at)
    if observed.utcoffset() is None:
        raise ValueError('披露参考核对时刻须含时区')
    return max(end, observed).isoformat(timespec='milliseconds')


class PublicationSource(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always')
    role: Literal['report', 'publication_notice']
    url: str = Field(pattern=r'^https://\S+$', min_length=9)
    sha256: str = Field(pattern=DIGEST)


def validate_publication_reference(report_period, published_on, reference_observed_at, sources):
    published = date.fromisoformat(published_on)
    year, quarter = int(report_period[:4]), int(report_period[-1])
    if year < 2000:
        raise ValueError('日期型披露证据不覆盖此历史范围')
    month = quarter * 3
    period_end = date(year, month, calendar.monthrange(year, month)[1])
    if published < period_end:
        raise ValueError('披露日早于对应报告期末')
    if published > datetime.fromisoformat(reference_observed_at).astimezone(ZoneInfo('Asia/Shanghai')).date():
        raise ValueError('披露日期晚于本次参考核对日')
    if {source.role for source in sources} != {'report', 'publication_notice'}:
        raise ValueError('披露证据缺少报告或实际披露公告')
    seen = {}
    for source in sources:
        if source.url in seen and seen[source.url] != source.sha256:
            raise ValueError('同一披露来源的内容摘要冲突')
        seen[source.url] = source.sha256
    return publication_available_at(published_on, reference_observed_at)


class DisclosureMatched(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always',
                              populate_by_name=True, alias_generator=to_camel)
    status: Literal['matched'] = 'matched'
    fund_symbol: str = Field(pattern=r'^\d{6}\.OF$')
    report_period: str = Field(pattern=r'^[2-9]\d{3}-Q[1-4]$')
    issuer: str = Field(min_length=1)
    precision: Literal['DATE_ONLY'] = 'DATE_ONLY'
    published_on: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    reference_observed_at: str = Field(pattern=CLOCK)
    available_at: str = Field(pattern=CLOCK)
    reference_version: str = Field(pattern=DIGEST)
    holdings_digest: str = Field(pattern=DIGEST)
    matched_count: int = Field(ge=1)
    reference_count: int = Field(ge=1)
    algorithm: Literal['exact_symbol_weight_v1'] = 'exact_symbol_weight_v1'
    sources: tuple[PublicationSource, ...] = Field(min_length=2)

    @model_validator(mode='after')
    def integrity(self):
        expected = validate_publication_reference(self.report_period, self.published_on,
                                                 self.reference_observed_at, self.sources)
        if datetime.fromisoformat(self.available_at) != datetime.fromisoformat(
                expected):
            raise ValueError('披露保守可用边界不匹配')
        if self.matched_count > self.reference_count:
            raise ValueError('匹配项数超过参考项数')
        return self


class DisclosureUnavailable(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, revalidate_instances='always',
                              populate_by_name=True, alias_generator=to_camel)
    status: Literal['unavailable'] = 'unavailable'
    reason_code: Literal['holdings_mismatch'] = 'holdings_mismatch'
    fund_symbol: str = Field(pattern=r'^\d{6}\.OF$')
    report_period: str = Field(pattern=r'^[2-9]\d{3}-Q[1-4]$')
    reference_version: str = Field(pattern=DIGEST)


DisclosureEvidence = Annotated[Union[DisclosureMatched, DisclosureUnavailable], Field(discriminator='status')]


def correlate_disclosure(evidence, symbol, report_period, digest, count, observed_at, disclosure_date):
    if evidence is None:
        return
    if (evidence.fund_symbol, evidence.report_period) != (symbol, report_period):
        raise ValueError('披露证据与基金或报告期不匹配')
    if isinstance(evidence, DisclosureMatched):
        if disclosure_date is not None:
            raise ValueError('日期证据不能覆盖精确披露时刻')
        if evidence.holdings_digest != digest or evidence.matched_count != count:
            raise ValueError('披露证据没有绑定本次持仓观察')
        if datetime.fromisoformat(evidence.reference_observed_at) > datetime.fromisoformat(observed_at):
            raise ValueError('披露参考核对时刻晚于本次来源观察')
