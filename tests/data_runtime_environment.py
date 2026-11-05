"""离线装配数据验收环境：复制已安装数据依赖原字节，保持版本及来源身份。"""

import importlib.metadata as metadata
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import venv

REPOSITORY = Path(__file__).resolve().parents[1]
FORBIDDEN_DISTRIBUTIONS = (
    "openai", "anthropic", "litellm", "google-genai", "google-generativeai", "langchain", "llama-index",
)
FORBIDDEN_MODULES = (
    "openai", "anthropic", "litellm", "google.genai", "google.generativeai", "langchain", "llama_index",
)
# HTTP/OAuth、控制加密、索引维护与原文解析的独立数据边界。
EXTRA_ROOTS = ("cryptography", "pypinyin", "python-multipart", "httpx", "lxml-html-clean")


def data_distribution_roots():
    from src.data.runtime_inventory import data_runtime_inventory

    inventory = data_runtime_inventory(REPOSITORY)
    modules = {item["module"].split(".")[0] for item in inventory["imports"]}
    modules -= sys.stdlib_module_names | {"src", "api", "data_provider", "tests", "scripts", "__future__"}
    owners = metadata.packages_distributions()
    unknown = sorted(module for module in modules if not owners.get(module))
    unowned = [module for module in unknown if importlib.util.find_spec(module) is not None]
    if unowned:
        raise RuntimeError("已安装数据模块缺少发行包归属：" + ",".join(unowned))
    roots = sorted(set(EXTRA_ROOTS) | {name for module in modules if module in owners for name in owners[module]})
    return roots, unknown


def distribution_closure(roots):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name

    pending = list(roots)
    selected = {}
    while pending:
        name = canonicalize_name(pending.pop())
        if name in selected:
            continue
        if any(name == prefix or name.startswith(prefix + "-") for prefix in FORBIDDEN_DISTRIBUTIONS):
            raise RuntimeError("数据依赖引入模型 SDK：" + name)
        distribution = metadata.distribution(name)
        selected[name] = distribution
        for raw in distribution.requires or ():
            requirement = Requirement(raw)
            if requirement.marker is None or requirement.marker.evaluate({"extra": ""}):
                pending.append(requirement.name)
    return selected


def build_data_runtime_environment(root, *, with_tests=False):
    root = Path(root).resolve()
    allowed = (Path(tempfile.gettempdir()).resolve(), Path("/private/tmp"))
    if not root.name.startswith("dsa-data-runtime-") or not any(root.is_relative_to(base) for base in allowed):
        raise ValueError("数据验收环境只能位于自有临时目录")
    target = root / "python"
    if target.exists():
        raise ValueError("不覆盖已有 Python 环境")
    roots, missing = data_distribution_roots()
    if with_tests:
        roots.append("pytest")
    selected = distribution_closure(roots)
    venv.EnvBuilder(with_pip=False, system_site_packages=False, symlinks=True).create(target)
    executable = target / "bin/python"
    site = subprocess.check_output(
        [str(executable), "-c", "import site;print(site.getsitepackages()[0])"], text=True,
    ).strip()
    site = Path(site)
    count = 0
    for distribution in selected.values():
        for relative in distribution.files or ():
            relative = Path(relative)
            if relative.is_absolute() or ".." in relative.parts or relative.suffix in {".pth", ".pyc"}:
                continue
            source = Path(distribution.locate_file(relative))
            if not source.is_file():
                continue
            destination = site / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                if destination.read_bytes() != source.read_bytes():
                    raise RuntimeError("数据发行包文件冲突：" + str(relative))
                continue
            # 来源校验要求真实常规文件；不借助符号链接绕过包源码身份门禁。
            shutil.copy2(source, destination)
            count += 1
    report = {"python": str(executable), "roots": roots, "copiedFiles": count, "testTools": with_tests,
              "baselineMissingModules": missing,
              "packages": {name: item.version for name, item in sorted(selected.items())},
              "forbiddenDistributions": list(FORBIDDEN_DISTRIBUTIONS), "forbiddenModules": list(FORBIDDEN_MODULES)}
    (root / "environment.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return report


if __name__ == "__main__":
    root = Path(tempfile.mkdtemp(prefix="dsa-data-runtime-"))
    report = build_data_runtime_environment(root)
    print(json.dumps({"root": str(root), "python": report["python"], "packages": len(report["packages"]),
                      "copiedFiles": report["copiedFiles"]}, ensure_ascii=False))
