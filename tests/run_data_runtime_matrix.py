"""在物理数据环境中执行 D0 数据类别回归；完整日志留在自有临时目录。"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from tests.data_runtime_environment import build_data_runtime_environment
from scripts.data_test_scope import load_scope

REPOSITORY = Path(__file__).resolve().parents[1]


def matrix_files():
    return load_scope(REPOSITORY)[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", help="已装配、包含 pytest 的物理数据环境解释器")
    parser.add_argument("files", nargs="*", help="失败后的定向复核；默认完整数据矩阵")
    options = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="dsa-data-runtime-"))
    executable = options.python
    if not executable:
        executable = build_data_runtime_environment(root, with_tests=True)["python"]
    files = options.files or matrix_files()
    assert all((REPOSITORY / name.split("::")[0]).is_file() and name.startswith("tests/") for name in files)
    env_file = root / "empty.env"
    env_file.write_text("")
    environment = {
        "PATH": os.environ.get("PATH", ""), "LANG": "en_US.UTF-8", "HOME": str(root),
        "PYTHONPATH": str(REPOSITORY), "PYTHONNOUSERSITE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "DSA_DATA_RUNTIME_PYTHON": executable, "ENV_FILE": str(env_file),
        "DATABASE_PATH": str(root / "default-data.db"), "LOG_DIR": str(root / "logs"),
        "STOCK_INDEX_REMOTE_UPDATE_ENABLED": "false",
    }
    # HOME 使用隔离缓存目录，Node 仍使用已安装的 Volta 工具链。
    volta_home = os.environ.get("VOLTA_HOME", str(Path.home() / ".volta"))
    if Path(volta_home).is_dir():
        environment["VOLTA_HOME"] = volta_home
    driver = (
        "from tests.data_runtime_process import install_network_boundary; "
        "import pytest,sys; raise SystemExit(pytest.main(sys.argv[1:]))"
    )
    log = root / "pytest.log"
    junit = root / "results.xml"
    with log.open("w") as output:
        result = subprocess.run([executable, "-c", driver, "-q", "-m", "not network", "--tb=short", "--disable-warnings",
                                 "-p", "no:cacheprovider", "--junitxml=" + str(junit), *files],
                                cwd=REPOSITORY, env=environment, stdout=output, stderr=subprocess.STDOUT)
    summary = {"exitCode": result.returncode, "python": executable, "files": files,
               "log": str(log), "junit": str(junit)}
    (root / "matrix.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = log.read_text().splitlines()
    print(json.dumps({**summary, "files": len(files)}, ensure_ascii=False))
    print("\n".join(line for line in lines if re.search(r"(?:failed|passed|skipped|ERROR tests/|FAILED tests/)", line))[-4000:])
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
