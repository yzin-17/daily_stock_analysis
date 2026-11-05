from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from scripts.ci_test_shard import (
    REPO_ROOT,
    discover_test_files,
    load_durations,
    partition_test_files,
)
from scripts import ci_test_shard
from scripts.data_test_scope import load_scope


def test_real_ci_shards_are_complete_disjoint_and_balanced() -> None:
    test_files = discover_test_files()
    durations = load_durations()
    groups, totals = partition_test_files(test_files, durations, 3)

    flattened = [test_file for group in groups for test_file in group]
    assert sorted(flattened) == test_files
    assert len(flattened) == len(set(flattened))
    assert all(group == sorted(group) for group in groups)
    assert max(totals) - min(totals) < 1.0
    scope, _ = load_scope()
    assert set(test_files) == set(scope["dataFiles"]) | set(scope["partialFiles"])
    assert not set(test_files) & {path for group in scope["retiredByOwner"].values() for path in group["files"]}


def test_new_test_file_uses_fallback_without_being_dropped() -> None:
    groups, totals = partition_test_files(
        ["tests/test_a.py", "tests/test_b.py", "tests/test_new.py"],
        {"tests/test_a.py": 3.0, "tests/test_b.py": 1.0},
        2,
    )

    assert sorted(test_file for group in groups for test_file in group) == [
        "tests/test_a.py",
        "tests/test_b.py",
        "tests/test_new.py",
    ]
    assert totals == pytest.approx([3.0, 3.0])


def test_partition_accounts_for_first_shard_preflight_cost() -> None:
    groups, totals = partition_test_files(
        ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "tests/test_d.py"],
        {
            "tests/test_a.py": 4.0,
            "tests/test_b.py": 4.0,
            "tests/test_c.py": 2.0,
            "tests/test_d.py": 2.0,
        },
        2,
        initial_totals=[4.0, 0.0],
    )

    assert groups == [
        ["tests/test_b.py"],
        ["tests/test_a.py", "tests/test_c.py", "tests/test_d.py"],
    ]
    assert totals == pytest.approx([8.0, 8.0])


@pytest.mark.parametrize("splits", [0, -1])
def test_invalid_split_count_is_rejected(splits: int) -> None:
    with pytest.raises(ValueError, match="splits must be positive"):
        partition_test_files(["tests/test_a.py"], {}, splits)


def test_invalid_initial_totals_are_rejected() -> None:
    with pytest.raises(ValueError, match="initial_totals"):
        partition_test_files(["tests/test_a.py"], {}, 2, initial_totals=[0.0])


def test_duration_file_tracks_current_baseline_and_valid_paths() -> None:
    duration_path = REPO_ROOT / ".github" / "ci-test-durations.json"
    assert duration_path.is_file()
    durations = load_durations(duration_path)

    assert len(durations) >= 250
    assert all((REPO_ROOT / Path(test_file)).is_file() for test_file in durations)
    assert max(durations.values()) >= 30


def test_shard_cli_executes_only_its_selected_data_files(monkeypatch):
    calls = []
    monkeypatch.setattr(ci_test_shard.subprocess, "call", lambda args, **kwargs: calls.append(args) or 0)
    for group in range(1, 4):
        assert ci_test_shard.main(["--splits", "3", "--group", str(group), "--", "-q"]) == 0
    selected = [arg for args in calls for arg in args if arg.startswith("tests/")]
    assert sorted(selected) == discover_test_files()
    assert len(selected) == len(set(selected))


@pytest.mark.parametrize("sharded", [False, True])
def test_ci_gate_passes_all_targets_only_without_sharding(tmp_path, sharded):
    executable = tmp_path / "python"
    log = tmp_path / "calls.jsonl"
    executable.write_text(f"#!{sys.executable}\n" + '''import json, os, subprocess, sys
with open(os.environ["TEST_ARGV_LOG"], "a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["scripts/data_test_scope.py"]:
    raise SystemExit(subprocess.call([os.environ["TEST_REAL_PYTHON"], *sys.argv[1:]]))
''')
    executable.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + ":" + os.environ.get("PATH", ""),
               TEST_ARGV_LOG=str(log), TEST_REAL_PYTHON=sys.executable)
    env.pop("PYTEST_SPLITS", None)
    env.pop("PYTEST_GROUP", None)
    if sharded:
        env.update(PYTEST_SPLITS="3", PYTEST_GROUP="2")
    result = subprocess.run(["bash", "scripts/ci_gate.sh", "offline-tests"], cwd=REPO_ROOT,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    command = commands[-1]
    if sharded:
        assert command[0] == "scripts/ci_test_shard.py"
        assert not any(arg.startswith("tests/") for arg in command)
    else:
        _, expected = load_scope()
        assert [arg for arg in command if arg.startswith("tests/")] == expected
