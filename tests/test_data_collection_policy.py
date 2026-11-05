"""验证项目默认测试收集会在导入产品前执行明确退役合同。"""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def collect(*arguments):
    env = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONNOUSERSITE="1")
    return subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", *arguments],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)


def test_explicit_product_file_is_rejected_before_its_removed_import():
    result = collect("tests/test_codex_app_server_gate_a.py")
    assert result.returncode == 4
    assert "产品测试已按明确清单退役" in result.stderr
    assert "ModuleNotFoundError" not in result.stdout + result.stderr


def test_partial_product_selector_is_rejected_but_data_selectors_remain():
    retired = "test_daily_analysis_workflow_matches_documented_searxng_variable_mapping"
    result = collect("tests/test_searxng_env_docs.py::" + retired)
    assert result.returncode == 4
    assert "产品测试断言已按明确清单退役" in result.stderr
    result = collect("tests/test_searxng_env_docs.py")
    assert result.returncode == 0, result.stdout + result.stderr
    assert retired not in result.stdout
    assert "test_env_example_documents_dedicated_searxng_configuration" in result.stdout
    assert "test_changelog_mentions_searxng_actions_variable_mapping" in result.stdout
