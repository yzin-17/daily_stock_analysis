"""直接、延迟函数、包导出及动态导入都不能绕过数据边界。"""

import pytest

from scripts.check_data_service_boundary import check_boundary


def fixture(root, source):
    (root / "data_provider").mkdir()
    (root / "server.py").write_text(source)
    return root


def test_actual_runtime_closure_and_syntax():
    assert check_boundary(syntax=True)["runtimeFiles"] > 100


@pytest.mark.parametrize("source", [
    "from src.config import get_config", "import openai", "import google.genai",
    "def late():\n    from src.core.pipeline import StockAnalysisPipeline\n",
    "import importlib\nimportlib.import_module('src.agent')",
    "import importlib\ndef late(name):\n    return importlib.import_module(name)\n",
    "def late(name):\n    return __import__(name)\n",
])
def test_forbidden_or_unreviewed_import_fails_before_execution(tmp_path, source):
    with pytest.raises(ValueError):
        check_boundary(fixture(tmp_path, source))


def test_package_lazy_exports_do_not_hide_product_dependency(tmp_path):
    fixture(tmp_path, "from src.services import StockService")
    package = tmp_path / "src/services"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        "def __getattr__(name):\n    _lazy_map = {'StockService': 'src.services.stock_service'}\n",
    )
    with pytest.raises(ValueError, match="产品"):
        check_boundary(tmp_path)


def test_data_relative_imports_are_compiled_and_missing_sdk_is_not_imported(tmp_path):
    fixture(tmp_path, "from data_provider.reader import value")
    (tmp_path / "data_provider/__init__.py").write_text("")
    (tmp_path / "data_provider/reader.py").write_text("from .values import value\n")
    (tmp_path / "data_provider/values.py").write_text("import requests\nvalue = 1\n")
    assert check_boundary(tmp_path, syntax=True)["runtimeFiles"] == 4
