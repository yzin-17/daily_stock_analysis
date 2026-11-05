"""用假 Docker CLI 核对隔离脚本的资源边界与失败回收；不运行 Docker。"""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_DOCKER = r'''
import json,os,sys
from pathlib import Path
root=Path(os.environ['CONTROLLED_DOCKER_ROOT']);args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as output:output.write(json.dumps(args)+'\n')
path=root/'state.json';state=json.loads(path.read_text()) if path.exists() else {}
fault=os.environ.get('CONTROLLED_DOCKER_FAULT','')
def save():path.write_text(json.dumps(state))
if args[:2]==['image','inspect']:
 if '--format' in args:print('unknown' if fault=='label' else 'dsa-data-runtime-v1')
elif args[:2]==['container','inspect']:
 if '--format' not in args:raise SystemExit(0 if fault=='collision' or state.get('created') else 1)
 if not state.get('created'):raise SystemExit(1)
 if '.State.ExitCode' in args[args.index('--format')+1]:print('137' if fault=='exit' else '0')
 else:print(state['name'])
elif args[0]=='run':
 mount=args[args.index('--mount')+1]
 assert mount.startswith('type=bind,src=') and mount.endswith(',dst=/tmp/dsa-smoke.env,readonly')
 empty=Path(mount.split(',')[1][4:]);assert empty.is_file() and empty.read_bytes()==b''
 assert 'ENV_FILE=/tmp/dsa-smoke.env' in args
 state['empty_env']=str(empty)
 state.update(created=True,name=args[args.index('--name')+1]);save()
 if fault=='run':raise SystemExit(1)
 print('controlled-container')
elif args[0]=='build':pass
elif args[0]=='exec':
 if fault=='health' or ('-i' in args and fault=='probe'):raise SystemExit(1)
elif args[0]=='stop':state['stopped']=True;save()
elif args[0]=='rm':state['removed']=True;save()
elif args[0]=='logs':print('controlled-startup-failure')
else:raise AssertionError(args)
'''


def run_script(tmp_path, fault="", *, build=False):
    binary = tmp_path / "bin"
    binary.mkdir()
    docker = binary / "docker"
    docker.write_text("#!" + sys.executable + "\n" + FAKE_DOCKER)
    docker.chmod(0o755)
    arguments = ["bash", "scripts/verify_data_image.sh"]
    if build:
        arguments.append("--build")
    result = subprocess.run([*arguments, "controlled:data"], cwd=ROOT,
                            env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                                 "CONTROLLED_DOCKER_ROOT": str(tmp_path), "CONTROLLED_DOCKER_FAULT": fault,
                                 "DSA_SMOKE_POLL_ATTEMPTS": "1", "DSA_IMAGE_BUILD_PYTHON": sys.executable},
                            capture_output=True, text=True, timeout=10)
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    return result, calls


def test_isolation_and_ownership_cleanup_contract(tmp_path):
    result, calls = run_script(tmp_path)
    assert result.returncode == 0, result.stderr
    run = next(args for args in calls if args[0] == "run")
    assert "--pull=never" in run and "--read-only" in run
    assert run[run.index("--network") + 1] == "none"
    assert "-v" not in run and "--volume" not in run and "-p" not in run
    assert {"/app/data:rw,uid=1000,gid=1000", "/app/logs:rw,uid=1000,gid=1000",
            "/app/reports:rw,uid=1000,gid=1000", "/home/dsa:rw,uid=1000,gid=1000"} <= set(run)
    name = run[run.index("--name") + 1]
    assert [args for args in calls if args[0] == "rm"] == [["rm", "-f", name]]
    assert any(args[0] == "stop" for args in calls)
    state = json.loads((tmp_path / "state.json").read_text())
    assert not Path(state["empty_env"]).exists()


@pytest.mark.parametrize("fault", ["run", "health", "probe", "exit"])
def test_failure_still_removes_only_owned_container(tmp_path, fault):
    result, calls = run_script(tmp_path, fault)
    assert result.returncode != 0
    run = next(args for args in calls if args[0] == "run")
    assert [args for args in calls if args[0] == "rm"] == [["rm", "-f", run[run.index("--name") + 1]]]


@pytest.mark.parametrize("fault", ["label", "collision"])
def test_preflight_failure_has_no_container_mutation(tmp_path, fault):
    result, calls = run_script(tmp_path, fault)
    assert result.returncode != 0
    assert not any(args[0] in {"run", "stop", "rm", "exec"} for args in calls)


def test_explicit_build_uses_same_reviewed_dockerfile_and_then_isolated_smoke(tmp_path):
    result, calls = run_script(tmp_path, build=True)
    assert result.returncode == 0, result.stderr
    build = next(args for args in calls if args[0] == "build")
    assert build[-1] == str(ROOT)
    assert build[build.index("-f") + 1] == str(ROOT / "docker/Dockerfile")
    assert calls.index(build) < next(index for index, args in enumerate(calls) if args[0] == "run")
