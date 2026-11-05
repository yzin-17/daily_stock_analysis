"""真实搜索装配在无模型/产品模块的进程中保留查询与数据诊断。"""

import os
from pathlib import Path
import subprocess
import sys

import pytest


PROBE = r"""
import importlib.abc, socket, sys, types
from datetime import datetime, timezone
forbidden = ("src.config", "src.agent", "src.llm", "src.notification", "src.report_language",
             "src.storage", "src.stock_analyzer", "litellm", "openai", "google.genai")
class BlockProducts(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == p or fullname.startswith(p + ".") for p in forbidden):
            raise AssertionError("data search imported " + fullname)
sys.meta_path.insert(0, BlockProducts())
def deny(self, address):
    raise AssertionError("data search attempted external connection")
socket.socket.connect = deny
calls = []
class Client:
    fail = False
    def __init__(self, api_key):
        assert api_key == "fixture-search-key"
    def search(self, **kwargs):
        assert kwargs["include_answer"] is False and kwargs["include_raw_content"] is False
        calls.append(kwargs)
        if self.fail:
            raise TimeoutError("controlled-timeout")
        return {"results": [{"title":"受控市场公告", "url":"https://controlled.invalid/news",
                "content":"保留原始新闻片段", "published_date":datetime.now(timezone.utc).isoformat()}]}
sdk = types.ModuleType("tavily")
sdk.TavilyClient = Client
sys.modules["tavily"] = sdk
from src.search_service import get_search_service, reset_search_service
from src.services.run_diagnostics import (
    activate_run_diagnostic_context, current_diagnostic_snapshot, reset_run_diagnostic_context,
)
service = get_search_service()
assert get_search_service() is service
if sys.argv[1] == "missing":
    assert not service.is_available
    assert not service.search_topic_news("受控市场").success
    assert calls == []
else:
    assert [provider.name for provider in service._providers] == ["Tavily"]
    token = activate_run_diagnostic_context(trace_id="data-search-controlled")
    try:
        first = service.search_topic_news("受控市场", max_results=1)
        assert first.success and first.results[0].snippet == "保留原始新闻片段"
        assert service.search_topic_news("受控市场", max_results=1).results == first.results
        assert len(calls) == 1 and calls[0]["topic"] == "news"
        Client.fail = True
        failed = service.search_topic_news("其他受控市场", max_results=1)
        assert not failed.success and not failed.results
        snapshot = current_diagnostic_snapshot()
        runs = snapshot["provider_runs"]
        assert any(run["provider"] == "Tavily" and run["success"] for run in runs)
        assert any(run["provider"] == "Tavily" and not run["success"] for run in runs)
        assert "fixture-search-key" not in repr(snapshot)
        assert len(calls) == 2
    finally:
        reset_run_diagnostic_context(token)
reset_search_service()
assert not any(name == p or name.startswith(p + ".") for name in sys.modules for p in forbidden)
print("data-search-isolation-ok")
"""


@pytest.mark.parametrize("mode", ["missing", "tavily"])
def test_real_search_configuration_transport_and_diagnostics_without_products(tmp_path, mode):
    values = ["SEARXNG_PUBLIC_INSTANCES_ENABLED=false"]
    if mode == "tavily":
        values.append("TAVILY_API_KEYS=fixture-search-key")
    env_file = tmp_path / "search.env"
    env_file.write_text("\n".join(values))
    result = subprocess.run(
        [sys.executable, "-c", PROBE, mode], cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "ENV_FILE": str(env_file)},
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr[-1800:]
    assert "data-search-isolation-ok" in result.stdout
    assert "fixture-search-key" not in result.stderr
