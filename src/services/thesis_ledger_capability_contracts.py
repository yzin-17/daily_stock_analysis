"""能力专属查询合同；现行 V3 载荷及冻结格式保持不变。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Literal, Mapping, Protocol, TYPE_CHECKING

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
    from src.services.thesis_ledger_status_acquisition import StatusAcquisitionCache


def provider_symbol_for_contract(symbol: str) -> str:
    """现行 Fetcher 的证券代码桥接；不推断证券身份或上市状态。"""
    value = symbol.strip().upper()
    if "." in value:
        base, suffix = value.rsplit(".", 1)
        if suffix in {"SH", "SS", "SZ", "BJ"} and base.isdigit():
            return base
        if suffix == "HK" and base.isdigit():
            return f"HK{base.zfill(5)}"
    for prefix in ("SH", "SZ", "SS", "BJ"):
        if value.startswith(prefix):
            remainder = value[len(prefix):]
            if remainder.startswith("."):
                remainder = remainder[1:]
            if remainder.isdigit():
                return remainder
    return value


class CapabilityContractError(ValueError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, diagnostics: dict[str, object] | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.diagnostics = dict(diagnostics or {})


class DailyParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    interactive_chart: bool = False
    historical_tradability: bool = False
    target_timeout_seconds: float = Field(default=4.5, gt=0, le=4.5)

    def get(self, name: str, default=None):
        return getattr(self, name, default)


class DailyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    capability: Literal["DAILY_BAR"] = "DAILY_BAR"
    symbol: str = Field(min_length=1)
    instrument_type: Literal["STOCK", "ETF", "INDEX"]
    timeframe: Literal["1d"] = "1d"
    adjustment: Literal["none", "qfq", "hfq"]
    start: str
    end: str
    request_id: str = Field(min_length=1)
    parameters: DailyParameters = Field(default_factory=DailyParameters)

    @model_validator(mode="after")
    def window(self):
        first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if first.isoformat() != self.start or last.isoformat() != self.end or first > last:
            raise ValueError("日线窗口必须是有序的 ISO 日期")
        return self

    @classmethod
    def from_request(cls, request):
        return cls.model_validate({name: getattr(request, name) for name in cls.model_fields})


class QuoteQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    symbol: str = Field(min_length=1)
    instrument_type: Literal["ETF", "STOCK", "INDEX"]


class QuoteValue(Protocol):
    price: float
    open_price: float
    high: float
    low: float
    pre_close: float
    volume: float | None
    amount: float | None
    provider_timestamp: str | None
    fetched_at: str


@dataclass(frozen=True)
class SourceProbeContext:
    capability: str
    snapshot: ProviderCredentialSnapshot | None
    sample_symbol: str
    adapter: object | None = None


@dataclass(frozen=True)
class SourceProbePolicy:
    read: Callable[[SourceProbeContext], None]
    instrument_types: tuple[tuple[str, Literal['STOCK', 'ETF', 'MUTUAL_FUND']], ...] = ()
    capabilities: tuple[str, ...] = ()
    adapter_required: bool = False


@dataclass(frozen=True)
class ExactProbeReadContext:
    key: Mapping[str, object]
    target: Mapping[str, object]
    policy_revision: int | None = None


class ExactProbeSample(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    symbol: str = Field(min_length=1)
    start: str
    end: str

    @model_validator(mode='after')
    def ordered_dates(self):
        first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if first.isoformat() != self.start or last.isoformat() != self.end or first > last:
            raise ValueError('精确检查样本日期必须是有序ISO日期')
        return self


@dataclass(frozen=True)
class CatalogManifestContext:
    provider: Mapping[str, object]
    key: Mapping[str, object]
    target: Mapping[str, object]
    has_exact_daily_binding: bool


@dataclass(frozen=True)
class CatalogReadinessContext:
    key: Mapping[str, object]
    target: Mapping[str, object]


@dataclass(frozen=True)
class PolicyAdmissionContext:
    key: Mapping[str, object]
    target: Mapping[str, object]
    manifest: Mapping[str, object]
    state: str
    payload: Mapping[str, object] | None
    read_credentials: Callable[[], ProviderCredentialSnapshot | None]


@dataclass(frozen=True)
class ResearchCalendarContext:
    symbol: str
    start: str
    end: str
    market: str
    asset_type: str


@dataclass(frozen=True)
class ResearchFrameContext:
    symbol: str
    frame: pd.DataFrame


@dataclass(frozen=True)
class ResearchFrameProjection:
    frame: pd.DataFrame

    def __post_init__(self):
        if not isinstance(self.frame, pd.DataFrame):
            raise CapabilityContractError('invalid_response', '来源研究投影未返回桥接帧')


@dataclass(frozen=True)
class EnvironmentCredentialPolicy:
    read: Callable[[], dict[str, str]]
    select_method: Callable[[Mapping[str, str]], str] | None = None
    snapshot_values: Callable[[Mapping[str, str]], dict[str, str]] | None = None


@dataclass(frozen=True)
class QuoteAdmissionContext:
    store: object
    key: Mapping
    target: Mapping
    provider_manifest: Mapping
    snapshot: ProviderCredentialSnapshot | None = None


@dataclass(frozen=True)
class QuoteGuardContext:
    store: object
    asset_type: str
    upstream_source: str
    symbol: str
    expected_policy_revision: int | None


@dataclass(frozen=True)
class QuoteExecutionGuard:
    snapshot: ProviderCredentialSnapshot
    still_current: Callable[[], bool]

    def __post_init__(self):
        from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
        if not isinstance(self.snapshot, ProviderCredentialSnapshot) or not callable(self.still_current):
            raise CapabilityContractError('invalid_response', '来源报价守卫未返回有效快照或复核器')


@dataclass(frozen=True)
class QuoteGuardPolicy:
    prepare: Callable[[QuoteGuardContext], QuoteExecutionGuard | None]
    rejected_message: str
    changed_message: str


@dataclass(frozen=True)
class ReadContext:
    adapter: object
    snapshot: ProviderCredentialSnapshot | None = None
    timeout_seconds: float = 4.5
    acquisition_cache: StatusAcquisitionCache | None = None
    control_epoch: tuple[int, int] | None = None
    probe: bool = False
    database_path: str | None = None


@dataclass(frozen=True)
class DailyRead:
    """来源内部的 V3 桥接结果；DataFrame 不作为新的产品合同。"""

    frame: pd.DataFrame
    pages_fetched: int

    def __post_init__(self):
        if not isinstance(self.frame, pd.DataFrame) or type(self.pages_fetched) is not int or self.pages_fetched < 1:
            raise CapabilityContractError("invalid_response", "日线处理器未返回标准桥接结果")
