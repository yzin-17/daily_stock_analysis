#!/usr/bin/env python3
"""构建带应用自有令牌存储接口的固定版本 Longbridge SDK。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

UPSTREAM = "https://github.com/longbridge/openapi.git"
REVISION = "68080b64ee02836e2fec625e9605dc5389abdb17"
TAG = "v4.5.0"
ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / "patches" / "longbridge-sdk"


def run(command: list[str], *, cwd: Path, env: dict[str, str], log: Path) -> str:
    print(f"构建步骤：{' '.join(command[:3])}", flush=True)
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
    with log.open("a", encoding="utf-8") as stream:
        stream.write(result.stdout)
        stream.write(result.stderr)
    if result.returncode:
        print("\n".join((result.stdout + result.stderr).splitlines()[-40:]), file=sys.stderr)
        raise RuntimeError(f"构建步骤失败：{command[0]}；详情见 {log}")
    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / "longbridge-sdk")
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--prepare-only", action="store_true", help="只验证固定源码与补丁可应用性")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    work = args.work_dir or Path(tempfile.mkdtemp(prefix="thesis-ledger-longbridge-"))
    work = work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    source = work / "source"
    if source.exists():
        raise RuntimeError("工作目录已有 source；请提供新的构建工作目录，以保留原有文件")
    log = work / "build.log"
    env = dict(os.environ)
    env.setdefault("CARGO_HOME", str(output / "cargo-cache"))
    env.setdefault("CARGO_TARGET_DIR", str(output / "cargo-target"))
    env.setdefault("PIP_CACHE_DIR", str(output / "pip-cache"))
    env.setdefault("CARGO_BUILD_JOBS", "1")
    env.setdefault("CARGO_PROFILE_RELEASE_LTO", "false")
    run(["git", "clone", "--depth", "1", "--branch", TAG, UPSTREAM, str(source)], cwd=work, env=env, log=log)
    revision = run(["git", "rev-parse", "HEAD"], cwd=source, env=env, log=log)
    if revision != REVISION:
        raise RuntimeError("SDK 上游 tag 与固定 revision 不符，停止构建")
    patch = str(PATCHES / "oauth-storage.patch")
    run(["git", "apply", "--check", patch], cwd=source, env=env, log=log)
    run(["git", "apply", patch], cwd=source, env=env, log=log)
    shutil.copyfile(PATCHES / "Cargo.lock", source / "Cargo.lock")
    if args.prepare_only:
        print(f"固定 SDK 源码与补丁检查通过：{source}")
        return
    run(["cargo", "test", "--locked", "-p", "longbridge-oauth", "thesis_ledger"], cwd=source, env=env, log=log)
    build_env = work / "build-env"
    run([sys.executable, "-m", "venv", str(build_env)], cwd=work, env=env, log=log)
    executable_dir = "Scripts" if os.name == "nt" else "bin"
    executable_name = "python.exe" if os.name == "nt" else "python"
    python = str(build_env / executable_dir / executable_name)
    run([
        python, "-m", "pip", "wheel", "--no-deps", "--wheel-dir", str(output),
        "--config-settings=build-args=--locked", str(source / "python"),
    ], cwd=work, env=env, log=log)
    wheels = sorted(output.glob("longbridge-4.5.0+thesisledger.1-*.whl"))
    if not wheels:
        raise RuntimeError(f"未生成预期版本的 wheel；详情见 {log}")
    run([python, "-m", "pip", "install", "--no-deps", str(wheels[-1])], cwd=work, env=env, log=log)
    run([python, str(ROOT / "scripts" / "verify_longbridge_native_storage.py")], cwd=work, env=env, log=log)
    print(f"SDK 构建完成：{wheels[-1]}\n构建日志：{log}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
