"""静态精确绑定；目录、修订及处理器查找共用一份事实。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, ClassVar, Generic, Literal, Mapping, TypeVar

from pydantic import BaseModel, ValidationError
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot
from src.services.thesis_ledger_capability_contracts import CatalogManifestContext, CatalogReadinessContext, EnvironmentCredentialPolicy, PolicyAdmissionContext, QuoteAdmissionContext, QuoteGuardPolicy, SourceProbePolicy
from .thesis_ledger_event_execution_context_v1 import EventExecutionContext, EventProbeReadContext
from .thesis_ledger_research_instrument_contract import ResearchInstrumentPolicy
from .thesis_ledger_quote_units_contract import QuotePublicUnits, QuotePublicUnitsContext
from .thesis_ledger_quote_units_contract import QuotePublicUnitsError
from .thesis_ledger_quote_result_contract import QuoteResult, QuoteUnitFailure
from .thesis_ledger_daily_units_contract import DailyUnitsPolicy
from .thesis_ledger_daily_window_contract import DailyWindowPlan, DailyWindowPlanningContext
from .thesis_ledger_account_access_contract import AccountAccessProbeContext

from .thesis_ledger_capability_contracts import (
    CapabilityContractError, DailyQuery, DailyRead, QuoteQuery, QuoteValue, ReadContext, ExactProbeReadContext, ResearchCalendarContext, ResearchFrameContext, ResearchFrameProjection,
)

RequestT = TypeVar("RequestT", bound=BaseModel)
ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class CapabilityContract(Generic[RequestT, ResultT]):
    capability: str
    request_schema: type[RequestT]
    validate_result: Callable[[object], ResultT]
    version: int = 1
    operations: tuple[str, ...] = ("query",)
    validate_binding: Callable[[RequestT, Mapping, Mapping], None] | None = None
    correlate_result: Callable[[RequestT, ResultT, Mapping], ResultT] | None = None


def validate_daily_result(result: object) -> DailyRead:
    if not isinstance(result, DailyRead):
        raise CapabilityContractError("invalid_response", "处理器结果与日线合同不匹配")
    return result


def validate_quote_result(result: object) -> QuoteResult:
    from .thesis_ledger_quote_validation import validate_quote
    return validate_quote(result)


DAILY_CONTRACT = CapabilityContract("DAILY_BAR", DailyQuery, validate_daily_result)
QUOTE_CONTRACT = CapabilityContract("REALTIME_QUOTE", QuoteQuery, validate_quote_result)


@dataclass(frozen=True)
class ExactKey:
    market: str
    asset_type: str
    capability: str
    adjustment: str | None = None

    def route_key(self):
        if self.capability == "DAILY_BAR":
            return {"kind": "bar", "market": self.market, "assetType": self.asset_type,
                    "capability": self.capability, "timeframe": "1d", "adjustment": self.adjustment}
        return {"kind": "data", "market": self.market, "assetType": self.asset_type, "capability": self.capability}


@dataclass(frozen=True)
class DailyBinding:
    contract: ClassVar = DAILY_CONTRACT
    key: ExactKey
    upstream_source: str
    adapter_revision: str
    source_revision: str
    protocol: str
    maximum_rows: int | None
    read: Callable[[DailyQuery, ReadContext], DailyRead]
    gated: bool = False
    operation: Literal["query"] = "query"
    contract_version: int = 1
    verify_transport: Callable[[object, str, str, str], int] | None = None
    source_contract: Mapping[str, object] | None = None
    research_calendar_eligible: Callable[[ResearchCalendarContext], bool] | None = None
    research_frame_projection: Callable[[ResearchFrameContext], ResearchFrameProjection] | None = None
    research_instrument_policy: ResearchInstrumentPolicy | None = None
    public_units_policy: DailyUnitsPolicy | None = None
    window_planner: Callable[[DailyWindowPlanningContext], DailyWindowPlan] | None = None


@dataclass(frozen=True)
class QuoteBinding:
    contract: ClassVar = QUOTE_CONTRACT
    key: ExactKey
    upstream_source: str
    adapter_revision: str
    source_revision: str
    read: Callable[[QuoteQuery, ReadContext], QuoteValue]
    gated: bool = True
    operation: Literal["query"] = "query"
    contract_version: int = 1
    legacy_probe_default: bool = False
    dependency_modules: tuple[str, ...] | None = None


@dataclass(frozen=True)
class QueryBinding(Generic[RequestT, ResultT]):
    execution_domain: ClassVar = 'current-data'
    contract: CapabilityContract[RequestT, ResultT]
    key: ExactKey
    upstream_source: str
    adapter_revision: str
    source_revision: str
    read: Callable[[RequestT, ReadContext], ResultT]
    operation: Literal["query"] = "query"
    gated: bool = True
    validate_parameters: Callable[[RequestT], None] | None = None
    query_support: BaseModel | None = None

    admission_required: bool | None = None

    @property
    def contract_version(self):
        return self.contract.version


@dataclass(frozen=True)
class EventBinding(Generic[RequestT, ResultT]):
    execution_domain: ClassVar = 'event'
    contract: CapabilityContract[RequestT, ResultT]
    key: ExactKey
    upstream_source: str
    read: Callable[[RequestT, EventExecutionContext], ResultT]
    revision_resolver: Callable[[str | None], Mapping[str, str] | None]
    wire_project: Callable[[ResultT], dict]
    operation: Literal['query'] = 'query'
    gated: bool = True
    admission_required: bool = True
    validate_parameters: Callable[[RequestT], None] | None = None
    query_support: BaseModel | None = None
    revision_facts: Mapping[str, str] | None = None
    current_admission: Callable[[object, Mapping, Mapping, Mapping], object] | None = None
    probe_read: Callable[[EventProbeReadContext], None] | None = None

    @property
    def contract_version(self):
        return self.contract.version

    @property
    def adapter_revision(self):
        return (self.revision_facts or self.revision_resolver(None) or {}).get('adapterRevision')

    @property
    def source_revision(self):
        return (self.revision_facts or self.revision_resolver(None) or {}).get('sourceRevision')


@dataclass(frozen=True)
class SourceDefinition:
    provider_id: str
    manifest: Callable[[], dict]
    factory: Callable[[ReadContext], object]
    bindings: tuple[DailyBinding | QuoteBinding | QueryBinding | EventBinding, ...]
    adapter_import: tuple[str, str] | None = None
    credentials: tuple[tuple[str, tuple[str, ...]], ...] = ()
    dependency_modules: tuple[str, ...] = ()
    factory_scope: Literal["route", "provider"] = "route"
    require_credential_revision_for_cache: bool = False
    allow_empty_environment_credentials: bool = False
    unscoped_factory: Callable[[ReadContext], object] | None = None
    cache_verified_daily_factory: bool = False
    normalize_credential_value: Callable[[str, str], str] | None = None
    credential_revision_kind: Literal['version', 'hmac'] = 'version'
    credential_revision_payload: Callable[[ProviderCredentialSnapshot], bytes | None] | None = None
    quote_guard_policy: QuoteGuardPolicy | None = None
    quote_admission_reader: Callable[[QuoteAdmissionContext], Mapping | None] | None = None
    probe_policy: SourceProbePolicy | None = None
    account_access_probe: Callable[[AccountAccessProbeContext], Mapping] | None = None
    account_access_snapshot: Callable[[ProviderCredentialSnapshot], ProviderCredentialSnapshot] | None = None
    catalog_manifest_policy: Callable[[CatalogManifestContext], bool | None] | None = None
    catalog_readiness: Callable[[CatalogReadinessContext], bool | None] | None = None
    environment_credential_policy: EnvironmentCredentialPolicy | None = None
    exact_probe_read: Callable[[ExactProbeReadContext], Mapping | None] | None = None
    policy_admission_guard: Callable[[PolicyAdmissionContext], bool] | None = None
    quote_public_units: Callable[[QuotePublicUnitsContext], QuotePublicUnits] | None = None
    quote_source_aliases: tuple[tuple[str, str], ...] = ()


class CapabilityRegistry:
    def __init__(self, sources: tuple[SourceDefinition, ...]):
        self.sources = sources
        self._bindings = {}
        providers = set()
        contracts = {}
        for source in sources:
            if not source.provider_id or source.provider_id in providers or not callable(source.factory):
                raise ValueError("来源重复或缺少工厂")
            providers.add(source.provider_id)
            if source.catalog_readiness is not None and not callable(source.catalog_readiness):
                raise ValueError('来源目录就绪检查器无效')
            if source.factory_scope not in {"route", "provider"}:
                raise ValueError("来源工厂作用域无效")
            if type(source.require_credential_revision_for_cache) is not bool:
                raise ValueError("来源工厂缓存条件无效")
            if type(source.allow_empty_environment_credentials) is not bool:
                raise ValueError("来源工厂环境凭据条件无效")
            if source.unscoped_factory is not None and not callable(source.unscoped_factory):
                raise ValueError('来源无目标准备工厂无效')
            if type(source.cache_verified_daily_factory) is not bool:
                raise ValueError('来源精确日线工厂缓存声明无效')
            if source.normalize_credential_value is not None and not callable(source.normalize_credential_value):
                raise ValueError('来源凭据字段归一化器无效')
            if source.credential_revision_kind not in {'version', 'hmac'}:
                raise ValueError('来源凭据准入修订策略无效')
            if source.credential_revision_payload is not None and not callable(source.credential_revision_payload):
                raise ValueError('来源凭据修订载荷构造器无效')
            guard_policy = source.quote_guard_policy
            environment = source.environment_credential_policy
            if environment is not None and (
                not isinstance(environment, EnvironmentCredentialPolicy) or not callable(environment.read)
                or environment.select_method is not None and not callable(environment.select_method)
                or environment.snapshot_values is not None and not callable(environment.snapshot_values)
            ):
                raise ValueError('来源环境凭据策略无效')
            if source.catalog_manifest_policy is not None and not callable(source.catalog_manifest_policy):
                raise ValueError('来源目录兼容策略无效')
            if source.exact_probe_read is not None and not callable(source.exact_probe_read):
                raise ValueError('来源精确只读检查声明无效')
            if source.account_access_probe is not None and not callable(source.account_access_probe):
                raise ValueError('来源账户访问检查声明无效')
            if source.account_access_snapshot is not None and not callable(source.account_access_snapshot):
                raise ValueError('来源账户检查凭证快照声明无效')
            if source.probe_policy is not None:
                probe = source.probe_policy
                if not isinstance(probe, SourceProbePolicy) or not callable(probe.read):
                    raise ValueError('来源只读诊断策略无效')
                seen_probe_capabilities = set()
                if type(probe.adapter_required) is not bool or not isinstance(probe.capabilities, tuple):
                    raise ValueError('来源诊断生命周期声明无效')
                if any(
                    not isinstance(capability, str) or not any(
                        binding.key.capability == capability for binding in source.bindings
                    ) for capability in probe.capabilities
                ) or len(set(probe.capabilities)) != len(probe.capabilities):
                    raise ValueError('来源诊断能力必须为唯一已绑定能力')
                for capability, asset in probe.instrument_types:
                    if capability in seen_probe_capabilities or not any(
                        binding.key.capability == capability and binding.key.asset_type == asset for binding in source.bindings
                    ):
                        raise ValueError('来源诊断健康维度必须为唯一有效能力组合')
                    seen_probe_capabilities.add(capability)
            if source.quote_admission_reader is not None and not callable(source.quote_admission_reader):
                raise ValueError('来源报价准入查找器无效')
            if source.policy_admission_guard is not None and not callable(source.policy_admission_guard):
                raise ValueError('来源政策准入守卫无效')
            if source.quote_public_units is not None and not callable(source.quote_public_units):
                raise ValueError('来源公开报价单位投影器无效')
            quote_sources = {binding.upstream_source for binding in source.bindings if isinstance(binding, QuoteBinding)}
            aliases = source.quote_source_aliases
            if (not isinstance(aliases, tuple) or any(not isinstance(item, tuple) or len(item) != 2
                    or not isinstance(item[0], str) or not item[0] or item[0] != item[0].strip().lower()
                    or not isinstance(item[1], str) or item[1] not in quote_sources for item in aliases)
                    or len({item[0] for item in aliases}) != len(aliases)):
                raise ValueError('来源报价别名必须唯一且指向本来源的精确报价')
            if guard_policy is not None and (
                not isinstance(guard_policy, QuoteGuardPolicy) or not callable(guard_policy.prepare)
                or not isinstance(guard_policy.rejected_message, str) or not guard_policy.rejected_message.strip()
                or not isinstance(guard_policy.changed_message, str) or not guard_policy.changed_message.strip()
            ):
                raise ValueError('来源报价守卫策略无效')
            quote_probe_defaults = set()
            for binding in source.bindings:
                contract = binding.contract
                if isinstance(binding, DailyBinding) and binding.research_calendar_eligible is not None and not callable(binding.research_calendar_eligible):
                    raise ValueError('来源研究日历资格校验器无效')
                if isinstance(binding, DailyBinding) and binding.research_frame_projection is not None and not callable(binding.research_frame_projection):
                    raise ValueError('来源研究帧投影器无效')
                if isinstance(binding, DailyBinding) and binding.public_units_policy is not None and not isinstance(binding.public_units_policy, DailyUnitsPolicy):
                    raise ValueError('来源日线单位策略无效')
                if isinstance(binding, DailyBinding) and binding.window_planner is not None and not callable(binding.window_planner):
                    raise ValueError('来源日线窗口规划器无效')
                if isinstance(binding, DailyBinding) and binding.research_instrument_policy is not None:
                    policy = binding.research_instrument_policy
                    if not isinstance(policy, ResearchInstrumentPolicy) or not callable(policy.supports_symbol) or not callable(policy.read):
                        raise ValueError('来源研究身份策略无效')
                if (not isinstance(contract.version, int) or isinstance(contract.version, bool)
                        or contract.version < 1 or not contract.operations
                        or len(set(contract.operations)) != len(contract.operations)
                        or any(operation not in {"query", "subscribe"} for operation in contract.operations)
                        or not isinstance(contract.request_schema, type)
                        or not issubclass(contract.request_schema, BaseModel) or not callable(contract.validate_result)):
                    raise ValueError("能力合同版本、操作或校验器无效")
                if (binding.key.capability != binding.contract.capability
                        or binding.operation != 'query' or binding.operation not in binding.contract.operations or binding.contract_version != contract.version
                        or not callable(binding.read) or not binding.upstream_source
                        or not isinstance(binding, EventBinding) and (not binding.adapter_revision or not binding.source_revision)):
                    raise ValueError("能力合同、操作或处理器不匹配")
                contract_key = (contract.capability, contract.version)
                if contract_key in contracts and contracts[contract_key] != contract:
                    raise ValueError("同一能力版本合同不一致")
                contracts[contract_key] = contract
                if isinstance(binding, EventBinding) and (not callable(binding.revision_resolver) or not callable(binding.wire_project)):
                    raise ValueError('事件绑定缺少修订解析或协议投影')
                if isinstance(binding, EventBinding):
                    if binding.probe_read is not None and not callable(binding.probe_read):
                        raise ValueError('事件来源精确诊断读取器无效')
                    if binding.current_admission is not None and not callable(binding.current_admission):
                        raise ValueError('事件当前准入解析器无效')
                    if binding.revision_facts is not None and (set(binding.revision_facts) != {'adapterRevision', 'sourceRevision'}
                            or any(not isinstance(value, str) or not value.strip() for value in binding.revision_facts.values())):
                        raise ValueError('事件静态接口修订事实无效')
                if isinstance(binding, (QueryBinding, EventBinding)) and (
                        not callable(contract.validate_binding)
                        or not callable(contract.correlate_result)
                        or binding.validate_parameters is not None and not callable(binding.validate_parameters)):
                    raise ValueError("查询绑定必须具有精确请求和结果关联校验，不能声明订阅")
                if isinstance(binding, (QueryBinding, EventBinding)) and binding.admission_required is not None and type(binding.admission_required) is not bool:
                    raise ValueError("查询准入事实必须为 bool 或 None")
                if isinstance(binding, (QueryBinding, EventBinding)) and binding.query_support is not None:
                    if not isinstance(binding.query_support, BaseModel) or getattr(binding.query_support, "capability", None) != binding.key.capability:
                        raise ValueError("来源参数资格与绑定能力不一致")
                    type(binding.query_support).model_validate(binding.query_support.model_dump())
                if isinstance(binding, DailyBinding) and binding.key.adjustment not in {"none", "qfq", "hfq"}:
                    raise ValueError("日线缺少精确价格口径")
                if binding.key.capability != 'DAILY_BAR' and binding.key.adjustment is not None:
                    raise ValueError("非日线能力不得携带日线价格口径")
                if isinstance(binding, QuoteBinding):
                    dependencies = binding.dependency_modules
                    if dependencies is not None and (
                        not isinstance(dependencies, tuple)
                        or any(not isinstance(module, str) or not module.strip() for module in dependencies)
                        or len(set(dependencies)) != len(dependencies)
                    ):
                        raise ValueError('精确报价运行依赖必须是唯一非空模块名元组')
                    if type(binding.legacy_probe_default) is not bool:
                        raise ValueError("旧报价探测默认事实必须为 bool")
                    if binding.legacy_probe_default:
                        if binding.key in quote_probe_defaults:
                            raise ValueError("同一报价目标只能有一个旧探测默认来源")
                        quote_probe_defaults.add(binding.key)
                identity = self._identity(binding.key.route_key(), source.provider_id, binding.upstream_source, binding.operation)
                if identity in self._bindings:
                    raise ValueError("精确能力绑定重复")
                self._bindings[identity] = binding

    @staticmethod
    def _identity(key: Mapping, provider: str, upstream: str, operation: str = "query"):
        return tuple(sorted(key.items())), provider, upstream, operation

    def resolve(self, key: Mapping, target: Mapping, operation: str = "query"):
        if operation not in {"query", "subscribe"}:
            raise CapabilityContractError("unsupported_capability", "该绑定未实现请求操作")
        binding = self._bindings.get(self._identity(key, target.get("providerId"), target.get("upstreamSource"), operation))
        if binding is None and operation == "subscribe":
            raise CapabilityContractError("unsupported_capability", "该来源未实现订阅")
        return binding

    def source(self, provider_id: str):
        return next((source for source in self.sources if source.provider_id == provider_id), None)

    def manifests(self):
        views = {}
        for source in self.sources:
            value = source.manifest()
            if value.get("providerId") != source.provider_id:
                raise ValueError("来源定义身份不一致")
            views[source.provider_id] = value
        return views

    def factory_for_route(self, provider_id: str, key: Mapping | None):
        source = self.source(provider_id)
        if source is None or key is None:
            return None
        if any(binding.key.route_key() == dict(key) for binding in source.bindings):
            return source.factory
        return None

    def routes(self, capability: str | None = None):
        for source in self.sources:
            for binding in source.bindings:
                if capability is None or binding.key.capability == capability:
                    yield binding.key.route_key(), {"providerId": source.provider_id, "upstreamSource": binding.upstream_source}, binding

    def daily(self, request, key, target, context: ReadContext) -> DailyRead:
        from .thesis_ledger_current_data_route import current_data_route_key
        binding = self.resolve(key, target)
        if not isinstance(binding, DailyBinding):
            raise CapabilityContractError("unsupported_capability", "未绑定该精确日线能力")
        try:
            query = DailyQuery.from_request(request)
        except (ValidationError, ValueError) as error:
            raise CapabilityContractError("invalid_request", "日线请求未通过能力合同") from error
        if any(key.get(name) != value for name, value in {
            "capability": query.capability, "assetType": query.instrument_type,
            "timeframe": query.timeframe, "adjustment": query.adjustment,
            "market": current_data_route_key(query.capability, query.instrument_type, query.symbol)["market"],
        }.items()):
            raise CapabilityContractError("invalid_request", "日线请求与绑定不匹配")
        result = binding.read(query, context)
        if not isinstance(result, DailyRead):
            raise CapabilityContractError("invalid_response", "处理器结果与日线合同不匹配")
        return result

    def quote(self, query: QuoteQuery, key, target, context: ReadContext) -> QuoteResult:
        from .thesis_ledger_current_data_route import current_data_route_key
        binding = self.resolve(key, target)
        if not isinstance(binding, QuoteBinding):
            raise CapabilityContractError("unsupported_capability", "未绑定该精确报价能力")
        market = current_data_route_key("REALTIME_QUOTE", query.instrument_type, query.symbol)["market"]
        if key.get("assetType") != query.instrument_type or key.get("market") != market:
            raise CapabilityContractError("invalid_request", "报价请求与绑定不匹配")
        raw = binding.read(query, context)
        result = QUOTE_CONTRACT.validate_result(raw)
        source = self.source(target['providerId'])
        reported = (result.source or '').strip().lower()
        upstream = dict(source.quote_source_aliases).get(reported) if source is not None else None
        units, unit_error = result.units, result.unit_error
        if isinstance(raw, QuoteResult) and raw.upstream_source is not None:
            if raw.upstream_source != binding.upstream_source:
                raise CapabilityContractError('invalid_response', '标准报价报告来源与绑定不符')
            upstream = raw.upstream_source
        if not isinstance(raw, QuoteResult) and source is not None and source.quote_public_units is not None:
            try:
                projected = source.quote_public_units(QuotePublicUnitsContext(raw, upstream))
                if not isinstance(projected, QuotePublicUnits):
                    raise QuotePublicUnitsError()
                units = QuotePublicUnits.model_validate(projected.model_dump())
            except QuotePublicUnitsError as error:
                unit_error = QuoteUnitFailure(message=str(error))
            except ValueError:
                unit_error = QuoteUnitFailure(message='报价单位合同无效')
        return QuoteResult.model_validate({**result.model_dump(), 'upstream_source': upstream,
                                          'units': units, 'unit_error': unit_error})

    def query(self, contract: CapabilityContract[RequestT, ResultT], request: RequestT,
              key: Mapping, target: Mapping, context: ReadContext) -> ResultT:
        binding = self.resolve(key, target)
        if not isinstance(binding, (QueryBinding, EventBinding)) or binding.contract != contract:
            raise CapabilityContractError("unsupported_capability", "未绑定请求的能力合同")
        try:
            value = request.model_dump() if isinstance(request, BaseModel) else request
            validated = contract.request_schema.model_validate(value)
            contract.validate_binding(validated, key, target)
            if binding.validate_parameters is not None:
                binding.validate_parameters(validated)
        except CapabilityContractError:
            raise
        except (ValidationError, ValueError) as error:
            raise CapabilityContractError("invalid_request", "请求未通过能力合同") from error
        result = binding.read(validated, context)
        try:
            return contract.correlate_result(validated, contract.validate_result(result), target)
        except (ValidationError, ValueError) as error:
            raise CapabilityContractError("invalid_response", "处理器结果未通过能力关联合同") from error
