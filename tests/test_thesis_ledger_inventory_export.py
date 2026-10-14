"""静态库存的安全边界与来源事实一致性；运行于独立子进程。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(code):
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                          env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=30)


def test_inventory_is_derived_and_excludes_test_sources():
    result = run("from scripts.export_thesis_ledger_capability_inventory import inventory; import json; print(json.dumps(inventory()))")
    assert result.returncode == 0, result.stderr
    value = json.loads(result.stdout)
    assert len(value["providers"]) == 20
    identities = [(json.dumps(row["key"], sort_keys=True), json.dumps(row["target"], sort_keys=True)) for row in value["exactRoutes"]]
    assert len(identities) == len(set(identities))
    assert {source["providerId"] for source in value["unifiedDefinitions"]} == {"tencent", "hithink", "baostock", "official-states", "efinance", "akshare", "pytdx", "tickflow", "tickflow-free", "alphavantage", "finnhub", "tushare", "yfinance", "longbridge", "futu", "official-rules", "official-hk-rules", "official-us-rules", "official-dividends", "rqdata"}
    assert not any(row["key"]["capability"] == "ORDER_BOOK" for row in value["exactRoutes"])
    assert not any(source["providerId"].startswith("fixture-") for source in value["providers"])
    assert value["legacyBranches"] and value["inputs"]
    modules = {row["path"]: row for row in value["productSourceModules"]}
    assert "data_provider/cninfo_announcements.py" in modules
    assert "data_provider/cninfo_announcement_query.py" in modules
    assert "exchange_calendars" in modules["src/services/thesis_ledger_multimarket_calendar.py"]["imports"]
    assert "src/services/portfolio_service.py" in modules
    assert "data_provider/futu_query_worker.py" in modules
    assert "data_provider/futu_daily.py" in modules
    assert any(endpoint["path"] == "/market/fx-rates" for endpoint in modules["api/thesis_ledger.py"]["endpoints"])
    assert any("YfinanceFetcher" in row["classes"] for row in value["nativeAnalysisDeclarations"])
    assert {row["path"] for row in value["inputs"]} >= set(modules)
    consumers = value["productConsumers"]
    if consumers["available"]:
        files = {row["path"]: row for row in consumers["files"]}
        assert "/api/v3/thesis-ledger/market/bars" in files[
            "apps/server/src/integration/dsa/dsa-data-capability-reader-v3.ts"]["endpoints"]
        assert all(row["sha256"] for row in consumers["files"])


@pytest.mark.parametrize("operation", ["open", "mkdir", "database", "network", "sdk"])
def test_static_inventory_blocks_side_effects_before_execution(tmp_path, operation):
    target = tmp_path / "should-not-exist"
    operations = {
        "open": f"open({str(target)!r}, 'w')",
        "mkdir": f"os.mkdir({str(target)!r})",
        "database": f"sqlite3.connect({str(target)!r})",
        "network": "socket.socket().connect(('127.0.0.1', 9))",
        "sdk": "FakeSdk()",
    }
    code = f"""
import os, sqlite3, socket, sys
from scripts.export_thesis_ledger_capability_inventory import deny_effects, deny_sdk_construction
class FakeSdk:
    __module__ = 'futu.quote'
    def __init__(self):
        open({str(target)!r}, 'w')
sys.addaudithook(deny_effects)
sys.setprofile(deny_sdk_construction)
try:
    {operations[operation]}
except RuntimeError:
    print('blocked')
else:
    raise AssertionError('副作用被执行')
"""
    result = run(code)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "blocked"
    assert not target.exists()
