# -*- coding: utf-8 -*-
"""Distribution contracts for the globally bundled Futu SDK."""

from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _workflow(relative_path: str) -> dict:
    return yaml.load(_read(relative_path), Loader=yaml.BaseLoader)


def _job_run_text(job: dict) -> str:
    return "\n".join(
        str(step.get("run", ""))
        for step in job.get("steps", [])
        if isinstance(step, dict)
    )


def test_futu_sdk_is_pinned_and_verified_across_linux_distributions() -> None:
    requirements = _read("requirements.txt")
    dockerfile = _read("docker/Dockerfile")
    ci = _workflow(".github/workflows/ci.yml")
    docker_publish = _workflow(".github/workflows/docker-publish.yml")
    manual_publish = _workflow(".github/workflows/ghcr-dockerhub.yml")

    assert requirements.count("futu-api==10.8.6808") == 1
    assert "from server import app; import futu" in dockerfile
    assert "import futu" in _job_run_text(ci["jobs"]["backend-tests"])
    assert "verify_data_image.sh" in _job_run_text(ci["jobs"]["docker-build"])
    assert "import futu" in _read("scripts/verify_data_image.sh")
    assert "verify_data_image.sh" in _job_run_text(docker_publish["jobs"]["build-and-push"])
    assert "verify_data_image.sh" in _job_run_text(manual_publish["jobs"]["build-and-push"])
