"""统一能力接缝门禁：生产/测试隔离、来源依赖方向和单一登记事实。"""

import ast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES  # noqa: E402
from src.services.thesis_ledger_capability_registry import DailyBinding  # noqa: E402
from src.services.thesis_ledger_market_v3_adapters import _ROUTE_ADJUSTMENTS, _GATED_ROUTE_ADJUSTMENTS  # noqa: E402
from src.services.thesis_ledger_market_v3_revisions import _MARKET_V3_ADAPTER_REVISIONS  # noqa: E402
from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS  # noqa: E402
from src.services.thesis_ledger_native_daily import NATIVE_DAILY_ROUTES  # noqa: E402
from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory  # noqa: E402


def check():
    errors = []
    if NATIVE_DAILY_ROUTES:
        errors.append("退役 Native 日线库存不得重新登记；新来源应使用能力绑定")
    identities = [(tuple(sorted(key.items())), tuple(sorted(target.items())))
                  for key, target, _ in exact_route_inventory()]
    if len(identities) != len(set(identities)):
        errors.append("精确目标在目录中重复登记")
    quote_path = ROOT / "src/services/thesis_ledger_quote_dispatch.py"
    for node in ast.walk(ast.parse(quote_path.read_text())):
        if isinstance(node, ast.Attribute) and node.attr in {"fetch_quote", "get_realtime_quote"}:
            errors.append(f"报价公共分发:{node.lineno} 不得透传供应商 SDK 方法")
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("data_provider"):
            errors.append(f"报价公共分发:{node.lineno} 不得依赖供应商 SDK")
    runtime_tree = ast.parse((ROOT / "src/services/thesis_ledger_provider_runtime.py").read_text())
    smoke_tree = ast.parse((ROOT / 'src/services/thesis_ledger_provider_smoke.py').read_text())
    if any(isinstance(node, ast.Constant) and node.value == 'hithink' for node in ast.walk(smoke_tree)):
        errors.append('公共连接诊断不得恢复HiThink专属执行或健康维度分支')
    if any(isinstance(node, ast.Constant) and node.value in {'tushare', 'tickflow', 'finnhub', 'alphavantage'}
           for node in ast.walk(smoke_tree)):
        errors.append('公共连接诊断不得恢复已迁移报价/日线来源参数分支')
    revision_tree = ast.parse((ROOT / 'src/services/provider_credential_revision.py').read_text())
    market_revision_tree = ast.parse((ROOT / 'src/services/thesis_ledger_market_v3_revisions.py').read_text())
    catalog_manifest_tree = ast.parse((ROOT / 'src/services/thesis_ledger_catalog_manifest_v3.py').read_text())
    if any(isinstance(node, ast.Constant) and node.value == 'hithink' for node in ast.walk(catalog_manifest_tree)):
        errors.append('公共目录manifest不得恢复HiThink来源兼容分支')
    control_tree = ast.parse((ROOT / 'src/services/thesis_ledger_control.py').read_text())
    environment_tree = ast.parse((ROOT / 'src/services/thesis_ledger_environment_credentials.py').read_text())
    environment_providers = {'hithink', 'tushare', 'tickflow', 'finnhub', 'alphavantage', 'longbridge'}
    if any(isinstance(node, ast.Constant) and node.value in environment_providers for node in ast.walk(environment_tree)):
        errors.append('公共环境凭据读取不得恢复来源字段或方法名单')
    for function in ast.walk(control_tree):
        if isinstance(function, ast.FunctionDef) and function.name in {
            '_environment_credential_state', '_credential_state', 'provider_credential_snapshot'
        } and any(isinstance(node, ast.Constant) and node.value in environment_providers for node in ast.walk(function)):
            errors.append('Control凭据快照不得恢复来源字段或方法分支')
        if isinstance(function, ast.FunctionDef) and function.name == '_v3_target_reason' and any(
            isinstance(node, ast.Constant) and node.value == 'hithink' for node in ast.walk(function)
        ):
            errors.append('Control目标资格不得恢复HiThink供应商豁免')
    if any(isinstance(node, ast.Constant) and node.value == 'hithink' for node in ast.walk(market_revision_tree)):
        errors.append('公共修订不得恢复不可达的来源修订fallback')
    exact_evidence_tree = ast.parse((ROOT / 'src/services/thesis_ledger_exact_evidence_probe.py').read_text())
    exact_probe_tree = ast.parse((ROOT / 'src/services/thesis_ledger_exact_probe_read.py').read_text())
    dependency_tree = ast.parse((ROOT / 'src/services/thesis_ledger_provider_dependencies.py').read_text())
    readiness_tree = ast.parse((ROOT / 'src/services/thesis_ledger_local_rules.py').read_text())
    if any(isinstance(node, ast.ImportFrom) and (node.module or '').startswith((
            'data_provider', 'src.services.thesis_ledger_public_fees', 'src.services.thesis_ledger_trading_status',
            'src.services.thesis_ledger_multimarket_rules')) for node in ast.walk(readiness_tree)):
        errors.append('公共目录就绪不得直接选择来源读取器或文件路径')
    price_access_tree = ast.parse((ROOT / 'src/services/thesis_ledger_price_route_access.py').read_text())
    if any(isinstance(node, ast.Constant) and node.value in {'hithink', 'tencent'} for node in ast.walk(price_access_tree)):
        errors.append('公共价格/政策准入不得恢复已迁移报价fallback或来源后置凭据守卫')
    research_calendar_tree = ast.parse((ROOT / 'src/services/thesis_ledger_research_calendar.py').read_text())
    if any(isinstance(node, ast.Constant) and node.value in {'yfinance', 'hithink', 'tencent', 'yahoo-gme-share-window', 'yahoo-hk-share-window'}
           for node in ast.walk(research_calendar_tree)):
        errors.append('公共研究日历不得恢复来源名称或按股包选择分支')
    research_frame_tree = ast.parse((ROOT / 'src/services/thesis_ledger_zero_volume_observations.py').read_text())
    if any((isinstance(node, ast.Constant) and node.value in {'yfinance', 'yahoo-hk-share-window', 'responseSha256', 'excluded_zero_volume_observations'})
           or (isinstance(node, ast.Attribute) and node.attr == 'attrs') for node in ast.walk(research_frame_tree)):
        errors.append('公共研究帧选择不得恢复来源名称或私有元信息解析')
    for relative in ('src/services/thesis_ledger_reference_identity.py', 'src/services/thesis_ledger_dependency_facts.py'):
        instrument_tree = ast.parse((ROOT / relative).read_text())
        if any(isinstance(node, ast.Constant) and node.value in {'yfinance', 'yahoo-gme-share-window', 'yahoo-hk-share-window'}
               for node in ast.walk(instrument_tree)):
            errors.append('公共研究身份/单位不得恢复价格供应商名单或有界源分派')
        if any(isinstance(node, ast.ImportFrom) and (node.module or '').startswith('data_provider.yahoo_') for node in ast.walk(instrument_tree)):
            errors.append('公共研究身份/单位不得直接解析来源包；事实来源标签仍应如实保留')
    manifest_builder_tree = ast.parse((ROOT / 'src/services/provider_manifest.py').read_text())
    if any(isinstance(node, ast.Constant) and node.value in {'akshare', 'sina'} for node in ast.walk(manifest_builder_tree)):
        errors.append('通用manifest构造器不得恢复供应商资产例外；来源视图应从绑定派生')
    quote_units_tree = ast.parse((ROOT / 'api/thesis_ledger_quote_units.py').read_text())
    daily_units_tree = ast.parse((ROOT / 'api/thesis_ledger_source_basis.py').read_text())
    window_tree = ast.parse((ROOT / 'api/thesis_ledger_multi_window_v3.py').read_text())
    if any(isinstance(node, ast.Constant) and node.value in {'hithink', 'fund-market-historical'} for node in ast.walk(window_tree)):
        errors.append('公共日线窗口调度不得恢复来源名单或专有分窗算法')
    if any(isinstance(node, ast.ImportFrom) and (node.module or '').startswith(('data_provider', 'src.services.thesis_ledger_hithink'))
           for node in ast.walk(window_tree)):
        errors.append('公共日线窗口调度不得依赖来源算法')
    if any(isinstance(node, ast.Constant) and node.value in {'hithink', 'akshare', 'hithink_etf_field_units', 'native_daily_contract'}
           for node in ast.walk(daily_units_tree)):
        errors.append('日线公开单位不得恢复来源名称或私有合同字段解析')
    if any(isinstance(node, ast.ImportFrom) and (node.module or '').startswith(('data_provider', 'src.services.thesis_ledger_hithink'))
           for node in ast.walk(daily_units_tree)):
        errors.append('日线公开单位不得直接依赖来源解析器')
    if any(isinstance(node, ast.Constant) and node.value in {'hithink', 'price_currency', 'turnover_currency'} for node in ast.walk(quote_units_tree)):
        errors.append('公开报价单位不得恢复供应商或SDK字段解析')
    quote_identity_tree = ast.parse((ROOT / 'api/thesis_ledger_source_identity.py').read_text())
    if any(isinstance(node, ast.Constant) and node.value in {'futu', 'akshare_em', 'akshare_sina', 'efinance', 'akshare_qq', 'hithink/a-share-prices-snapshot'}
           for node in ast.walk(quote_identity_tree)):
        errors.append('公开报价来源不得恢复SDK供应商别名表或默认目标身份')
    quote_validation_tree = ast.parse((ROOT / 'src/services/thesis_ledger_quote_validation.py').read_text())
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'setattr'
           for node in ast.walk(quote_validation_tree)):
        errors.append('报价标准结果不得原位修改来源对象')
    quote_result_tree = ast.parse((ROOT / 'src/services/thesis_ledger_quote_result_contract.py').read_text())
    if any(isinstance(node, ast.ImportFrom) and (node.module or '').startswith(('data_provider', 'api.'))
           for node in ast.walk(quote_result_tree)):
        errors.append('报价标准结果不得反向依赖SDK或HTTP适配器')
    quote_api_tree = ast.parse((ROOT / 'api/thesis_ledger.py').read_text())
    for function in ast.walk(quote_api_tree):
        if isinstance(function, ast.FunctionDef) and function.name == '_real_quote' and any(
            isinstance(node, ast.Constant) and node.value == 'hithink' for node in ast.walk(function)
        ):
            errors.append('公开报价HTTP不得恢复供应商单位条件')
    if any(isinstance(node, ast.Constant) and node.value in {'akshare', 'baostock'} for node in ast.walk(dependency_tree)):
        errors.append('公共安装依赖检查不得恢复已迁移供应商模块表或目标特判')
    if any(isinstance(node, ast.Constant) and node.value in {'rqdata', 'hithink', 'tushare', 'akshare'} for node in ast.walk(exact_evidence_tree)):
        errors.append('公共事件精确诊断不得恢复已迁移来源读取器分支')
    if any(isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith('official-')
           for tree in (exact_evidence_tree, exact_probe_tree) for node in ast.walk(tree)):
        errors.append('公共精确证据检查不得恢复已迁移官方来源的供应商路径/工厂/前缀分支')
    for function in ast.walk(runtime_tree):
        if not isinstance(function, ast.FunctionDef) or function.name != 'execute_request':
            continue
        for node in ast.walk(function):
            if isinstance(node, ast.Attribute) and node.attr in {'get_daily_data_for_source', 'get_daily_data', 'get_daily_data_v2_raw'}:
                errors.append('通用Runtime请求不得恢复已退役日线SDK执行')
            if isinstance(node, ast.Constant) and node.value in {'get_daily_data_for_source', 'get_daily_data_v2_raw'}:
                errors.append('通用Runtime请求不得恢复已退役日线SDK透传')
    if any(isinstance(node, ast.Constant) and node.value in {'hithink', 'tushare', 'rqdata'}
           for node in ast.walk(revision_tree)):
        errors.append('公共凭据指纹不得恢复来源认证载荷分支')
    for node in ast.walk(runtime_tree):
        if (isinstance(node, ast.Compare) and any(isinstance(operator, (ast.In, ast.NotIn)) for operator in node.ops)
                and any(isinstance(value, ast.Constant) and value.value == 'tushare' for value in ast.walk(node))):
            errors.append('Runtime不得恢复供应商集合选择凭据准入策略')
        if isinstance(node, ast.Name) and node.id in {'current_hithink_quote_guard', 'hithink_quote_guard_still_current',
                                                    'hithink_quote_revisions', 'hithink_quote_admission_matches_current'}:
            errors.append('Runtime不得恢复来源专属报价生命周期')
    for node in ast.walk(ast.parse((ROOT / 'src/services/provider_credentials.py').read_text())):
        if isinstance(node, ast.Constant) and node.value == 'rqdata':
            errors.append('公共凭据规则不得恢复RQ来源特判或重复登记')
    event_tree = ast.parse((ROOT / 'src/services/thesis_ledger_event_v3.py').read_text())
    for node in ast.walk(event_tree):
        if isinstance(node, ast.Constant) and node.value == 'official-dividends':
            errors.append('事件公共执行不得恢复已迁移公告来源特判')
        if isinstance(node, ast.Constant) and node.value == 'rqdata':
            errors.append('事件公共执行不得恢复已迁移RQ来源特判')
        if isinstance(node, ast.Constant) and node.value == 'tushare':
            errors.append('事件公共执行不得恢复已迁移Tushare来源特判')
        if isinstance(node, ast.Constant) and node.value == 'hithink':
            errors.append('事件公共执行不得恢复已迁移HiThink来源特判')
        if isinstance(node, ast.Name) and node.id == 'execute_hithink_events_v3':
            errors.append('事件公共执行不得恢复HiThink Runtime透传')
        if isinstance(node, ast.Name) and node.id == 'execute_tushare_events_v3':
            errors.append('事件公共执行不得恢复Tushare Runtime透传')
        if isinstance(node, ast.Name) and node.id == 'read_rqdata_events_v3':
            errors.append('事件公共执行不得恢复RQ Runtime透传')
        if isinstance(node, ast.ImportFrom) and (node.module or '').endswith('thesis_ledger_notice_events_v3'):
            errors.append('事件公共执行不得直接依赖已迁移公告执行器')
        if isinstance(node, ast.Name) and node.id == 'fetch_fund_dividend_observations':
            errors.append('事件公共执行不得直接调用已迁移东财现金供应商读取')
        if isinstance(node, ast.Name) and node.id == 'read_mapped_split_events':
            errors.append('事件公共执行不得恢复已迁移拆分供应商读取')
    retired_public = {"_fund_nav_from_provider", "_validate_fund_nav", "_validate_fund_nav_history", "bound_nav_dates",
                      "_fund_holdings_from_provider", "_validate_fund_holdings", "_validate_chip_summary", "_fetch_hithink_etf_bars", "local_rule_operation"}
    for name in ("thesis_ledger_provider_runtime.py", "thesis_ledger_current_data_execution.py",
                 "thesis_ledger_exact_probe_read.py", "thesis_ledger_provider_smoke.py", "thesis_ledger_exact_bar_execution.py", "thesis_ledger_local_rules.py", "thesis_ledger_reference_data.py"):
        for node in ast.walk(ast.parse((ROOT / "src/services" / name).read_text())):
            if (isinstance(node, ast.FunctionDef) and node.name in retired_public
                    or isinstance(node, ast.Attribute) and node.attr in retired_public):
                errors.append(f"{name}:{node.lineno} 不得恢复已退役公共 SDK/原字段兼容执行")
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
           and node.func.id == "HiThinkSnapshotAdapter" for node in ast.walk(runtime_tree)):
        errors.append("Runtime 不得重新手工构造已迁移 HiThink 报价 SDK")
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("thesis_ledger_native_daily"):
                errors.append(f"{path.relative_to(ROOT)}:{node.lineno} 生产仍依赖退役 Native 日线接缝")
    paths = [ROOT / "src/services/thesis_ledger_capability_sources.py",
             ROOT / "src/services/thesis_ledger_capability_registry.py",
             ROOT / "src/services/thesis_ledger_bar_query_semantics.py",
             *sorted((ROOT / "data_provider/thesis_ledger_sources").glob("*.py"))]
    for path in paths:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                if module.startswith(("tests", "api")) or module.endswith("thesis_ledger_provider_runtime"):
                    errors.append(f"{path.relative_to(ROOT)}:{node.lineno} 反向依赖或生产测试混用 {module}")
    for source in CAPABILITY_SOURCES.sources:
        for binding in source.bindings:
            key = binding.key
            triple = (key.asset_type, source.provider_id, binding.upstream_source)
            if isinstance(binding, DailyBinding):
                if (key.market, key.asset_type, source.provider_id, binding.upstream_source) in NATIVE_DAILY_ROUTES:
                    errors.append(f"{triple} 已迁移日线仍在原生精确库存重复登记")
                if any(triple in inventory for inventory in (_ROUTE_ADJUSTMENTS, _GATED_ROUTE_ADJUSTMENTS, _MARKET_V3_ADAPTER_REVISIONS)):
                    errors.append(f"{triple} 已迁移日线仍在中央表重复登记")
            elif (key.market, key.asset_type, key.capability, source.provider_id, binding.upstream_source) in _DATA_ADAPTERS:
                errors.append(f"{source.provider_id}/{key.capability} 已迁移数据仍在中央表重复登记")
            if key.capability == "ORDER_BOOK":
                errors.append("隔离盘口进入生产目录")
    return errors


if __name__ == "__main__":
    errors = check()
    if errors:
        print("\n".join(errors))
        raise SystemExit(1)
    print("统一能力边界检查通过")
