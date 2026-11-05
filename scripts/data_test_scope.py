"""显式管理纯数据 CI 测试范围；新增、遗漏和混合断言不能默认跳过。"""

import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCOPE_PATH = "tests/data-test-scope.json"


def test_functions(path):
    result = set()
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            result.add(node.name)
        elif isinstance(node, ast.ClassDef):
            result.update(node.name + "::" + method.name for method in node.body
                          if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and method.name.startswith("test_"))
    return result


def load_scope(root=ROOT):
    root = Path(root)
    scope = json.loads((root / SCOPE_PATH).read_text())
    if scope["schemaVersion"] != "dsa-data-tests-v1":
        raise ValueError("数据测试范围版本无效")
    registered = list(scope["dataFiles"]) + list(scope["partialFiles"])
    for owner, group in scope["retiredByOwner"].items():
        if not owner or not group["reason"].strip():
            raise ValueError("产品测试退役缺少责任与理由")
        registered.extend(group["files"])
    if len(registered) != len(set(registered)):
        raise ValueError("测试路径重复登记")
    for value in registered:
        path = Path(value)
        if (path.is_absolute() or ".." in path.parts or any(char.isspace() for char in value)
                or not value.startswith("tests/") or not path.name.startswith("test_") or path.suffix != ".py"):
            raise ValueError("测试路径不符合安全约定：" + value)
    current = {str(path.relative_to(root)) for path in (root / "tests").rglob("test_*.py")}
    if current != set(registered):
        raise ValueError("测试路径未登记或已丢失：" + json.dumps(sorted(current ^ set(registered)), ensure_ascii=False))
    nodes = list(scope["dataFiles"])
    for value, partial in scope["partialFiles"].items():
        data = partial["dataSelectors"]
        retired = partial["retiredSelectors"]
        if not data or not retired or len(set(data) | set(retired)) != len(data) + len(retired):
            raise ValueError("混合测试必须明确且不重叠地分配断言：" + value)
        if set(data) | set(retired) != test_functions(root / value):
            raise ValueError("混合测试存在未登记的断言：" + value)
        if any(not reason.strip() for reason in retired.values()):
            raise ValueError("混合测试退役缺少理由：" + value)
        nodes.extend(value + "::" + name for name in data)
    # D0 的纯数据标记不能在后续范围清单中被静默转为产品退役。
    if scope["baselineInventory"] != "docs/evidence/2026-10-19-data-service-inventory.jsonl":
        raise ValueError("数据测试基线不是已登记的 D0 库存")
    baseline = root / scope["baselineInventory"]
    if sha256(baseline.read_bytes()).hexdigest() != scope["baselineSha256"]:
        raise ValueError("D0 库存内容与冻结摘要不一致")
    reviews = scope["dataRetentionReviews"]
    baseline_items = [json.loads(line) for line in baseline.read_text().splitlines()]
    retained_paths = {item["path"] for item in baseline_items if item.get("kind") == "path"
                      and item["ownerLeaf"] in {"数据保留", "D2d"}}
    retained_paths.add("scripts/ci_test_shard.py")
    for item in baseline_items:
        dependencies = item.get("directDependencies", [])
        only_data = dependencies and set(dependencies) <= retained_paths
        if item.get("kind") != "test" or ("数据保留" not in item.get("ownerLeaves", []) and not only_data):
            continue
        value = item["path"]
        if value in registered and value not in scope["dataFiles"] and value not in scope["partialFiles"]:
            if not reviews.get(value, "").strip():
                raise ValueError("D0 数据断言退役缺少独立所有权复核：" + value)
    return scope, sorted(nodes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="逐行输出应执行的测试路径或精确断言")
    options = parser.parse_args()
    try:
        scope, nodes = load_scope()
    except (KeyError, ValueError, OSError) as error:
        parser.exit(1, "数据测试范围检查失败：" + str(error) + "\n")
    if options.list:
        print("\n".join(nodes))
    else:
        print(json.dumps({"dataFiles": len(scope["dataFiles"]), "partialFiles": len(scope["partialFiles"]),
                          "retiredFiles": sum(len(group["files"]) for group in scope["retiredByOwner"].values()),
                          "testTargets": len(nodes)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
