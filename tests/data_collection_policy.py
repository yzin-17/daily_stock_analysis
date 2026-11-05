"""让默认 pytest 与 CI 共同消费已逐路径审查的数据测试清单。"""

from pathlib import Path

import pytest

from scripts.data_test_scope import ROOT, load_scope


def _policy(config):
    cached = getattr(config, "_dsa_data_collection_policy", None)
    if cached is None:
        try:
            scope, _ = load_scope()
        except (KeyError, ValueError, OSError) as error:
            raise pytest.UsageError("数据测试清单无效：" + str(error)) from error
        cached = (scope, {path for group in scope["retiredByOwner"].values() for path in group["files"]})
        config._dsa_data_collection_policy = cached
    return cached


def _relative_path(path):
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return None


def pytest_configure(config):
    scope, retired = _policy(config)
    for argument in config.args:
        path, *selector = str(argument).split("::", 1)
        relative = _relative_path(Path(config.invocation_params.dir) / path)
        if relative in retired:
            raise pytest.UsageError("产品测试已按明确清单退役：" + relative)
        partial = scope["partialFiles"].get(relative)
        if partial and selector and selector[0].split("[", 1)[0] in partial["retiredSelectors"]:
            raise pytest.UsageError("产品测试断言已按明确清单退役：" + relative)


def pytest_ignore_collect(collection_path, config):
    _, retired = _policy(config)
    if _relative_path(collection_path) in retired:
        return True
    return None


def pytest_collection_modifyitems(config, items):
    scope, retired = _policy(config)
    kept, deselected = [], []
    for item in items:
        relative = _relative_path(item.path)
        selector = item.nodeid.split("::", 1)[-1].split("[", 1)[0]
        partial = scope["partialFiles"].get(relative)
        if relative in retired or (partial and selector in partial["retiredSelectors"]):
            deselected.append(item)
        else:
            kept.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept
