"""只读导出当前产品能力；拒绝 SDK 实例化、网络、进程与配置写入。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
SDK_PREFIXES = ("futu", "longbridge", "baostock", "tushare", "yfinance", "akshare", "efinance", "rqdatac")


def deny_effects(event, args):
    if event in {"socket.connect", "socket.bind", "subprocess.Popen", "os.system", "sqlite3.connect", "os.mkdir",
                 "os.remove", "os.rmdir", "os.rename", "os.symlink", "os.link", "os.chmod", "os.chown", "os.truncate"}:
        raise RuntimeError(f"静态库存禁止副作用：{event}")
    if event == "open":
        mode, flags = args[1], args[2]
        if (isinstance(mode, str) and any(value in mode for value in "wax+")) or (
                isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
            raise RuntimeError("静态库存禁止文件写入")


def deny_sdk_construction(frame, event, arg):
    if event == "call" and frame.f_code.co_name in {"__init__", "__new__"}:
        instance = frame.f_locals.get("self")
        owner = type(instance)
        if (owner.__module__.startswith(SDK_PREFIXES) or frame.f_globals.get("__name__", "").startswith(SDK_PREFIXES)) or any(
                parent.__name__ in {"BaseFetcher", "DataFetcherManager"} for parent in owner.__mro__):
            raise RuntimeError(f"静态库存禁止来源实例：{owner.__module__}.{owner.__name__}")


def source_files(additional=()):
    paths = set(additional)
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path:
            resolved = Path(path).resolve()
            if resolved.is_relative_to(ROOT) and resolved.relative_to(ROOT).parts[0] in {"src", "data_provider", "scripts", "api"}:
                paths.add(resolved)
    return [{"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in sorted(paths)]


def legacy_branches():
    result = []
    paths = sorted((ROOT / "src/services").glob("thesis_ledger_*.py"))
    for path in paths:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                identifiers = set()
                for part in ast.walk(node):
                    name = ast.unparse(part.left) if isinstance(part, ast.Compare) else ""
                    if isinstance(part, ast.Compare) and "provider" in name.lower():
                        for operand in part.comparators:
                            values = operand.elts if isinstance(operand, (ast.Tuple, ast.List, ast.Set)) else [operand]
                            identifiers.update(value.value for value in values if isinstance(value, ast.Constant) and isinstance(value.value, str))
                if identifiers:
                    result.append({"path": str(path.relative_to(ROOT)), "function": node.name,
                                   "line": node.lineno, "providers": sorted(identifiers)})
    return result


def product_source_modules():
    """从真实 API/适配模块递归提取本仓依赖，不执行这些模块。"""
    pending = list((ROOT / "api").glob("thesis_ledger*.py"))
    pending += list((ROOT / "src/services").glob("thesis_ledger*.py"))
    seen, result = set(), []
    while pending:
        path = pending.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text())
        dependencies, endpoints = set(), []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
                if node.level:
                    owner = path.relative_to(ROOT).parts[:-1]
                    module = ".".join((*owner[:len(owner) - node.level + 1], module))
                dependencies.add(module)
                candidate = ROOT.joinpath(*module.split(".")).with_suffix(".py")
                if candidate.is_file():
                    pending.append(candidate)
            elif isinstance(node, ast.Import):
                dependencies.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                # 精确工厂与隔离子进程使用模块名常量；只读取本仓现存模块，不执行 -m 入口。
                if re.fullmatch(r"(?:data_provider|src|api)\.[A-Za-z_][A-Za-z0-9_.]*", node.value):
                    candidate = ROOT.joinpath(*node.value.split(".")).with_suffix(".py")
                    if candidate.is_file():
                        dependencies.add(node.value)
                        pending.append(candidate)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                        if decorator.func.attr in {"get", "post", "put", "delete"} and decorator.args:
                            route = decorator.args[0]
                            if isinstance(route, ast.Constant) and isinstance(route.value, str):
                                endpoints.append({"method": decorator.func.attr.upper(), "path": route.value,
                                                  "handler": node.name, "line": node.lineno})
        result.append({"path": str(path.relative_to(ROOT)), "imports": sorted(dependencies), "endpoints": endpoints})
    return sorted(result, key=lambda row: row["path"]), seen


def native_analysis_declarations():
    """原生分析的独立入口仅记录声明，不将其备用链登记为产品目标。"""
    path = ROOT / "data_provider/__init__.py"
    tree = ast.parse(path.read_text())
    return [{"module": node.module, "classes": [alias.name for alias in node.names if alias.name.endswith("Fetcher")]}
            for node in tree.body if isinstance(node, ast.ImportFrom) and
            any(alias.name.endswith("Fetcher") for alias in node.names)]


def product_consumers():
    """读取相邻 Server 的实际依赖与 wire 路径；不存在时显式保留缺口。"""
    root = ROOT.parent / "thesis-ledger"
    directory = root / "apps/server/src"
    if not directory.is_dir():
        return {"available": False, "files": []}
    result = []
    for path in sorted(directory.rglob("*.ts")):
        source = path.read_text()
        imports = re.findall(r"from ['\"]([^'\"]*(?:integration/dsa|market-data-reader|dsa[^'\"]*)[^'\"]*)['\"]", source)
        endpoints = sorted(set(re.findall(r"/api/v3/thesis-ledger/[a-zA-Z0-9/_-]+", source)))
        if imports or endpoints:
            result.append({"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "imports": sorted(set(imports)), "endpoints": endpoints})
    return {"available": True, "files": result}


def binding_revisions(binding):
    from src.services.thesis_ledger_capability_registry import EventBinding
    if isinstance(binding, EventBinding):
        revisions = binding.revision_facts or binding.revision_resolver(None) or {}
        return {name: revisions.get(name) for name in ('adapterRevision', 'sourceRevision')}
    return {'adapterRevision': binding.adapter_revision, 'sourceRevision': binding.source_revision}


def inventory():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    sys.addaudithook(deny_effects)
    sys.setprofile(deny_sdk_construction)
    # 原包 __init__ 同时服务原生分析，库存只加载产品元数据子模块，避免实例化原生分析管理器。
    package = types.ModuleType("data_provider")
    package.__path__ = [str(ROOT / "data_provider")]
    sys.modules["data_provider"] = package
    try:
        from src.services.thesis_ledger_provider_manifests import PROVIDER_MANIFESTS
        from src.services.thesis_ledger_exact_route_inventory import exact_route_inventory
        from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
        definitions = []
        for source in CAPABILITY_SOURCES.sources:
            definitions.append({"providerId": source.provider_id, "factory": f"{source.factory.__module__}.{source.factory.__name__}",
                                "dependencies": source.dependency_modules, "credentials": source.credentials,
                                **({'credentialValueNormalizer': f'{source.normalize_credential_value.__module__}.{source.normalize_credential_value.__name__}'}
                                   if source.normalize_credential_value is not None else {}),
                                "bindings": [{"key": binding.key.route_key(), "upstreamSource": binding.upstream_source,
                                              "operation": binding.operation, "contractVersion": binding.contract_version,
                                              **binding_revisions(binding),
                                              **({"querySupport": binding.query_support.model_dump()}
                                                 if getattr(binding, "query_support", None) is not None else {}),
                                              **({"legacyProbeDefault": True}
                                                 if getattr(binding, "legacy_probe_default", False) else {}),
                                              "handler": f"{binding.read.__module__}.{binding.read.__name__}"}
                                             for binding in source.bindings]})
        exact = [{"key": key, "target": target, "gated": gated} for key, target, gated in exact_route_inventory()]
        providers = [{key: manifest.get(key) for key in (
            "providerId", "displayName", "version", "origin", "markets", "capabilities", "upstreamSources",
            "configurationMode", "requiresCredential")} for manifest in PROVIDER_MANIFESTS.values()]
        modules, inputs = product_source_modules()
        return {"inventoryVersion": 1, "mode": "static-read-only-no-sdk-instance-no-network", "providers": providers,
                "exactRoutes": exact, "unifiedDefinitions": definitions, "legacyBranches": legacy_branches(),
                "productSourceModules": modules, "nativeAnalysisDeclarations": native_analysis_declarations(),
                "productConsumers": product_consumers(),
                "inputs": source_files(inputs | {ROOT / "data_provider/__init__.py"})}
    finally:
        sys.setprofile(None)


if __name__ == "__main__":
    print(json.dumps(inventory(), ensure_ascii=False, sort_keys=True))
