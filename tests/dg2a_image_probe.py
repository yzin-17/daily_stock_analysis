"""在候选镜像中复用数据断言；测试工具独立装配，不修改生产依赖。"""

import argparse
from contextlib import contextmanager
from hashlib import sha256
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile

TOOLS = ("pytest", "pluggy", "iniconfig", "pygments")
PACKAGE_ROOTS = {"pytest", "_pytest", "pluggy", "iniconfig", "pygments"}
NODE_SCHEMA_CASE = (
    "tests/test_thesis_ledger_tushare_events_v3.py::"
    "test_http_success_consumes_actual_built_schema_exchange_without_rebuild"
)


def tool_bundle():
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz") as archive:
        for name in TOOLS:
            distribution = metadata.distribution(name)
            for item in distribution.files:
                path = PurePosixPath(str(item))
                if path.is_absolute() or ".." in path.parts or path.suffix in (".pyc", ".pyo"):
                    continue
                if path.parts[0] not in PACKAGE_ROOTS and not path.parts[0].endswith(".dist-info"):
                    continue
                source = Path(distribution.locate_file(item))
                if not source.is_file() or source.is_symlink():
                    continue
                assert source.suffix not in (".so", ".dylib", ".pyd"), "测试工具不能复制宿主原生扩展"
                data = source.read_bytes()
                info = tarfile.TarInfo(str(path))
                info.size = len(data)
                info.mode = 0o644
                archive.addfile(info, io.BytesIO(data))


def bootstrap():
    root = Path(tempfile.mkdtemp(prefix="dsa-dg2a-tools-"))
    size = 0
    with tarfile.open(fileobj=sys.stdin.buffer, mode="r|gz") as archive:
        for item in archive:
            path = PurePosixPath(item.name)
            assert item.isfile() and not path.is_absolute() and ".." not in path.parts
            assert path.parts[0] in PACKAGE_ROOTS or path.parts[0].endswith(".dist-info")
            size += item.size
            assert size <= 64 * 1024 * 1024
            target = root / item.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(item) as source:
                target.write_bytes(source.read())
    python = root / "python"
    python.write_text(
        "#!/bin/sh\n"
        f'export PYTHONPATH="{root}:/app${{PYTHONPATH:+:$PYTHONPATH}}"\n'
        'exec /usr/local/bin/python "$@"\n'
    )
    python.chmod(0o700)
    print(json.dumps({"directory": str(root), "python": str(python), "toolBytes": size,
                      "uid": os.geteuid(), "fileUid": python.stat().st_uid,
                      "mode": oct(python.stat().st_mode & 0o777),
                      "noexec": bool(os.statvfs(root).f_flag & os.ST_NOEXEC)}))


@contextmanager
def controlled_environment(tools):
    assert tools.parent == Path("/tmp") and tools.name.startswith("dsa-dg2a-tools-")
    with tempfile.TemporaryDirectory(prefix="dsa-dg2a-state-") as state:
        empty_env = Path(state) / "empty.env"
        empty_env.touch()
        previous = dict(os.environ)
        os.environ.clear()
        os.environ.update({
            "PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8", "HOME": state,
            "ENV_FILE": str(empty_env), "DATABASE_PATH": state + "/data.db", "LOG_DIR": state + "/logs",
            "PYTHONPATH": str(tools) + ":/app", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "DSA_DATA_RUNTIME_PYTHON": str(tools / "python"),
            "STOCK_INDEX_REMOTE_UPDATE_ENABLED": "false", "THESIS_LEDGER_FIXTURE_MODE": "false",
            "THESIS_LEDGER_DSA_TOKEN": "data-runtime-controlled-token",
            "THESIS_LEDGER_CONTROL_TOKEN": "data-runtime-controlled-control",
            "THESIS_LEDGER_DSA_SECRET_KEY": "dg2a-synthetic-encryption-key",
        })
        try:
            yield Path(state)
        finally:
            os.environ.clear()
            os.environ.update(previous)


def target_matrix(tools):
    sys.path.insert(0, str(tools))
    from scripts.data_test_scope import load_scope
    from tests.data_runtime_environment import FORBIDDEN_MODULES
    from tests.data_runtime_process import install_network_boundary, visible
    from longbridge.openapi import OAuthBuilder

    assert OAuthBuilder.THESIS_LEDGER_STORAGE_VERSION == 1
    assert not any(visible(name) for name in FORBIDDEN_MODULES)
    scope, _ = load_scope(Path("/app"))
    exact = {
        "test_data_runtime_acceptance.py", "test_data_config.py", "test_data_configuration_consumers.py",
        "test_data_fx_rates.py", "test_data_storage_isolation.py", "test_data_announcement_lifecycle.py",
        "test_data_catalog_lifecycle.py", "test_data_lifespan.py", "test_data_stock_index_http.py",
    }
    files = sorted(name for name in scope["dataFiles"] if Path(name).name in exact
                   or Path(name).name.startswith(("test_thesis_ledger_", "test_provider_oauth_")))
    assert exact <= {Path(name).name for name in files}
    assert "tests/test_provider_oauth_native_storage.py" in files
    install_network_boundary()
    with controlled_environment(tools):
        import pytest

        sys.executable = str(tools / "python")
        xml = tools / "result.xml"
        result = pytest.main(["-q", "-m", "not network", "--tb=short", "--disable-warnings",
                              "-p", "no:cacheprovider", "--junitxml=" + str(xml), "--deselect=" + NODE_SCHEMA_CASE, *files])
        from xml.etree import ElementTree

        tree = ElementTree.parse(xml)
        cases = tree.findall(".//testcase")
        native = [case for case in cases if "test_provider_oauth_native_storage" in case.get("classname", "")]
        native_passed = len(native) == 1 and not any(native[0].find(tag) is not None for tag in ("failure", "error", "skipped"))
        exit_code = int(result)
        if exit_code == 0 and not native_passed:
            exit_code = 1
        summary = {"exitCode": exit_code, "files": len(files), "cases": len(cases),
                   "failed": sum(case.find("failure") is not None for case in cases),
                   "errors": sum(case.find("error") is not None for case in cases),
                   "skipped": [case.get("classname") + "::" + case.get("name")
                               for case in cases if case.find("skipped") is not None],
                   "nativeStoragePassed": native_passed, "realModelCalls": 0, "network": "none",
                   "junit": str(xml),
                   "separateNodeSchemaCase": NODE_SCHEMA_CASE,
                   "toolVersions": {name: metadata.version(name) for name in TOOLS},
                   "testInputsSha256": sha256("\n".join(files).encode()).hexdigest()}
        print("DG2A_MATRIX " + json.dumps(summary, ensure_ascii=False), flush=True)
        return exit_code


def schema_exchange(tools):
    sys.path.insert(0, str(tools))
    import pytest
    from tests.data_runtime_process import install_network_boundary

    install_network_boundary()
    with controlled_environment(tools) as root, pytest.MonkeyPatch.context() as patches:
        import tests.conftest  # noqa: F401
        from tests.test_thesis_ledger_tushare_events_v3 import build_state, schema_exchange as exchange

        state = build_state(root, patches)
        with state.client:
            data = (json.dumps(exchange(state), ensure_ascii=False) + "\n").encode()
        path = tools / "tushare-schema-exchange.json"
        path.write_bytes(data)
        print("DG2A_EXCHANGE " + json.dumps({"path": str(path), "sha256": sha256(data).hexdigest()}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["tools", "bootstrap", "matrix", "serve", "exchange"])
    parser.add_argument("--tools", type=Path)
    parser.add_argument("--scenario", choices=["basics", "bars", "events", "nav", "rules", "reference"])
    args = parser.parse_args()
    if args.mode == "tools":
        tool_bundle()
    elif args.mode == "bootstrap":
        bootstrap()
    elif args.mode == "matrix":
        assert args.tools is not None
        return target_matrix(args.tools)
    elif args.mode == "exchange":
        assert args.tools is not None
        schema_exchange(args.tools)
    else:
        assert args.tools is not None and args.scenario is not None
        sys.path.insert(0, str(args.tools))
        with controlled_environment(args.tools):
            os.environ["DSA_DATA_RUNTIME_SCENARIO"] = args.scenario
            from tests.data_runtime_process import run

            run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
