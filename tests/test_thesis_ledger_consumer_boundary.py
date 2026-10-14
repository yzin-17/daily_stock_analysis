"""ThesisLedger consumer facade 与 Provider adapter 边界回归。"""

import ast
from pathlib import Path

from src.services.thesis_ledger_provider_runtime import ThesisLedgerProviderRuntime


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CONSUMER_FACADE_PATH = REPOSITORY_ROOT / "api" / "thesis_ledger.py"
PROVIDER_RUNTIME_PATH = (
    REPOSITORY_ROOT / "src" / "services" / "thesis_ledger_provider_runtime.py"
)


def _module_tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imported_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".", 1)[0])
    return names


def test_consumer_facade_has_no_native_manager_or_provider_sdk_bypass():
    """consumer facade 不能重新引入 native manager 或具体 Provider SDK。"""
    tree = _module_tree(CONSUMER_FACADE_PATH)
    imported = _imported_names(tree)
    forbidden_imports = {
        "akshare",
        "baostock",
        "efinance",
        "longbridge",
        "tushare",
        "yfinance",
    }
    assert not imported & forbidden_imports

    forbidden_names = {"DataFetcherManager", "_manager", "get_data_fetcher_manager"}
    forbidden_attributes = {
        "fund_open_fund_info_em",
        "get_chip_distribution",
        "get_daily_data",
        "get_quote_history",
        "get_realtime_quote",
    }
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden_names:
            violations.append(f"name:{node.id}@{node.lineno}")
        if isinstance(node, ast.Attribute) and node.attr in forbidden_names | forbidden_attributes:
            violations.append(f"attribute:{node.attr}@{node.lineno}")
    assert not violations, "consumer facade 存在越过 gateway 的调用: " + ", ".join(violations)


def test_standard_fund_nav_handler_delegates_sdk_read_only_to_source_adapter():
    """Fund NAV 数据通过来源 SDK 接缝转换为标准模型。"""

    class _Adapter:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_fund_nav_history(self, fund_code: str):
            self.calls.append(fund_code)
            import pandas as pd
            return pd.DataFrame({"日期": ["2026-01-01"], "单位净值": [1.2]})

    adapter = _Adapter()

    from src.services.thesis_ledger_fund_nav_query import read_latest_nav
    from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ProviderCallError
    result = read_latest_nav(ThesisLedgerDataRequest('FUND_NAV', '000001.OF'),
                             'akshare', adapter, 'eastmoney', ProviderCallError)

    assert result.point.unit_nav == 1.2
    assert result.symbol == "000001.OF"
    assert adapter.calls == ["000001"]


def test_provider_runtime_does_not_import_concrete_provider_sdk():
    """runtime 只调用 adapter；具体 SDK import 必须留在 adapter 模块内。"""
    tree = _module_tree(PROVIDER_RUNTIME_PATH)
    assert not _imported_names(tree) & {
        "akshare",
        "baostock",
        "efinance",
        "longbridge",
        "tushare",
        "yfinance",
    }
