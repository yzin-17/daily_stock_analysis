"""Catalog Provider 进程隔离、稳定错误分类和后续 Job 可用性回归。"""

import os
import time
from functools import partial
from pathlib import Path

import pytest

from src.services import thesis_ledger_catalog as catalog_module
from src.services.thesis_ledger_control import ThesisLedgerControlStore

# 完整离线集在 macOS spawn 子进程时有额外导入开销；本组验证终止与重试语义，
# 不以 1 秒内写出子进程标记作为业务时限。
_SPAWN_TOLERANT_TIMEOUT_SECONDS = 5.0


def _hanging_loader(marker_path: str) -> list[dict[str, str]]:
    Path(marker_path).write_text(str(os.getpid()), encoding="utf-8")
    while True:
        time.sleep(0.01)


def _failing_loader(counter_path: str) -> list[dict[str, str]]:
    path = Path(counter_path)
    current = int(path.read_text(encoding="utf-8")) if path.exists() else 0
    path.write_text(str(current + 1), encoding="utf-8")
    raise RuntimeError("provider is unavailable")


def _healthy_loader() -> list[dict[str, str]]:
    return [
        {
            "canonicalCode": "600519",
            "instrumentType": "STOCK",
            "market": "SH",
            "displayName": "贵州茅台",
        }
    ]


def _invalid_response_loader(counter_path: str):
    path = Path(counter_path)
    count = int(path.read_text()) if path.exists() else 0
    path.write_text(str(count + 1))
    raise catalog_module.CatalogBuildError(
        "invalid fixture", code="catalog_provider_invalid_response", retryable=False,
    )


def test_non_retryable_response_survives_process_boundary(tmp_path):
    counter = tmp_path / "invalid-attempts"
    with pytest.raises(catalog_module.CatalogBuildError) as error:
        catalog_module._bounded_call(partial(_invalid_response_loader, str(counter)),
                                     timeout_seconds=3, max_attempts=3, backoff_seconds=0)
    assert error.value.code == "catalog_provider_invalid_response"
    assert error.value.retryable is False
    assert counter.read_text() == "1"


def _wait_terminal(store: ThesisLedgerControlStore, job_id: str) -> dict:
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        result = store.get_catalog_job(job_id)
        if result["status"] in {"succeeded", "failed", "timeout"}:
            return result
        time.sleep(0.01)
    raise AssertionError(f"Catalog Job {job_id} did not reach a terminal state")


def _wait_process_gone(pid: int) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def test_timeout_terminates_hanging_provider_process(tmp_path):
    marker_path = tmp_path / "provider.pid"

    with pytest.raises(catalog_module.CatalogBuildError) as raised:
        catalog_module._bounded_call(
            partial(_hanging_loader, str(marker_path)),
            timeout_seconds=_SPAWN_TOLERANT_TIMEOUT_SECONDS,
            max_attempts=1,
        )

    assert raised.value.code == "catalog_provider_timeout"
    assert marker_path.exists()
    assert _wait_process_gone(int(marker_path.read_text(encoding="utf-8")))


def test_provider_retry_is_bounded_and_uses_stable_error(tmp_path):
    counter_path = tmp_path / "attempts.txt"

    with pytest.raises(catalog_module.CatalogBuildError) as raised:
        catalog_module._bounded_call(
            partial(_failing_loader, str(counter_path)),
            timeout_seconds=_SPAWN_TOLERANT_TIMEOUT_SECONDS,
            max_attempts=2,
            backoff_seconds=0.01,
        )

    assert raised.value.code == "catalog_provider_unavailable"
    assert counter_path.read_text(encoding="utf-8") == "2"


def test_hanging_provider_does_not_block_successful_catalog_provider(tmp_path):
    marker_path = tmp_path / "provider.pid"

    items, failures = catalog_module.build_catalog(
        {
            "hanging": partial(_hanging_loader, str(marker_path)),
            "healthy": _healthy_loader,
        },
        provider_timeout_seconds=_SPAWN_TOLERANT_TIMEOUT_SECONDS,
        max_attempts=1,
    )

    assert items == _healthy_loader()
    assert failures == {"hanging": "catalog_provider_timeout"}
    assert _wait_process_gone(int(marker_path.read_text(encoding="utf-8")))


def test_catalog_job_reports_provider_code_and_next_job_remains_available(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "false")
    store = ThesisLedgerControlStore(str(tmp_path / "catalog-timeout.db"))
    marker_path = tmp_path / "job-provider.pid"
    original_build_catalog = catalog_module.build_catalog

    def build_hanging_catalog():
        return original_build_catalog(
            {"hanging": partial(_hanging_loader, str(marker_path))},
            provider_timeout_seconds=_SPAWN_TOLERANT_TIMEOUT_SECONDS,
            max_attempts=1,
        )

    monkeypatch.setattr(catalog_module, "build_catalog", build_hanging_catalog)
    failed = _wait_terminal(store, store.trigger_catalog_job()["id"])

    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "catalog_all_providers_unavailable"
    assert failed["error"]["retryable"] is True
    assert _wait_process_gone(int(marker_path.read_text(encoding="utf-8")))

    monkeypatch.setenv("THESIS_LEDGER_FIXTURE_MODE", "true")
    recovered = _wait_terminal(store, store.trigger_catalog_job()["id"])
    assert recovered["status"] == "succeeded"
