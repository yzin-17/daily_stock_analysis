"""产品入口退役后，旧调用必须在读取配置或执行业务前终止。"""

import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("option", ["--schedule", "--serve-only", "--check-notify"])
def test_old_cli_is_absent_before_reading_environment(tmp_path, option):
    assert not (ROOT / "main.py").exists()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "ENV_FILE": str(tmp_path / "missing.env"),
           "PYTHONPATH": str(ROOT), "PYTHONNOUSERSITE": "1"}
    result = subprocess.run([sys.executable, "-S", str(ROOT / "main.py"), option],
                            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "ModuleNotFoundError" not in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_product_workflow_and_external_product_probes_are_removed():
    for path in ("webui.py", ".github/workflows/00-daily-analysis.yml",
                 "scripts/codex_app_server_gate_a.py", "scripts/e2e_test_feishu_app.py"):
        assert not (ROOT / path).exists()
    network = (ROOT / ".github/workflows/network-smoke.yml").read_text()
    assert "data_test_scope.py --list" in network
    assert "test.sh quick" not in network


def test_old_test_shortcut_cannot_start_model_or_notification(tmp_path):
    result = subprocess.run(["bash", str(ROOT / "scripts/test.sh"), "quick", "--no-notify"],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert "入口已退役" in result.stderr


def test_client_sources_and_release_entry_are_removed():
    for path in ("apps/dsa-web/package.json", "apps/dsa-desktop/package.json",
                 ".github/workflows/desktop-release.yml", "src/webui_frontend.py",
                 "scripts/build-backend-macos.sh", "scripts/build-backend.ps1"):
        assert not (ROOT / path).exists()
    assert (ROOT / "src/data/assets/stocks.index.json").is_file()
