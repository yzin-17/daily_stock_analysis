"""检查数据运行闭包的产品、模型及动态导入回流，支持仅编译数据输入。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data.runtime_inventory import data_runtime_inventory

FORBIDDEN = (
    "api.app", "api.v1", "bot", "strategies", "main", "src.config", "src.storage", "src.auth",
    "src.analyzer", "src.stock_analyzer", "src.market_analyzer", "src.agent", "src.llm",
    "src.notification", "src.scheduler", "src.core.pipeline", "src.report_language",
    "src.core.config_manager", "src.utils.market_review_region", "src.utils.sniper_points",
    "src.utils.sanitize", "src.utils.data_processing",
    "src.services.analysis_service", "src.services.analysis_context_builder", "src.services.backtest_service",
    "src.services.history_service", "src.services.stock_service", "src.services.task_service",
    "src.services.task_queue", "src.services.portfolio_service", "src.services.intelligence_service",
    "src.services.decision_signal_service", "src.services.system_config_service", "src.services.runtime_scheduler",
    "openai", "anthropic", "litellm", "google.genai", "google.generativeai", "langchain", "llama_index",
)
REVIEWED_DYNAMIC_IMPORTS = {
    ("src/services/thesis_ledger_provider_runtime.py", "_adapter"): "module_name",
}


def check_boundary(root=ROOT, *, syntax=False):
    root = Path(root)
    inventory = data_runtime_inventory(root)
    illegal = [item for item in inventory["imports"]
               if any(item["module"] == value or item["module"].startswith(value + ".") for value in FORBIDDEN)]
    if illegal:
        raise ValueError("数据闭包依赖产品或模型模块：" + json.dumps(illegal, ensure_ascii=False))
    for item in inventory["dynamicImports"]:
        if REVIEWED_DYNAMIC_IMPORTS.get((item["path"], item["function"])) != item["expression"]:
            raise ValueError("数据闭包存在未复核的动态导入：" + json.dumps(item, ensure_ascii=False))
    if syntax:
        for name in inventory["files"]:
            compile((root / name).read_text(), name, "exec")
    return {"runtimeFiles": len(inventory["files"]), "dynamicImports": len(inventory["dynamicImports"]),
            "syntax": syntax}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--syntax", action="store_true", help="只编译已核对的数据源码，不写入 pyc")
    options = parser.parse_args()
    try:
        print(json.dumps(check_boundary(syntax=options.syntax), ensure_ascii=False))
    except (ValueError, SyntaxError, OSError) as error:
        parser.exit(1, "数据边界检查失败：" + str(error) + "\n")


if __name__ == "__main__":
    main()
