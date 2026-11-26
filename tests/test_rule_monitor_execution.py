"""完整登记目录及扩展集合在隔离 SQLite 执行，网络完全受控。"""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import weakref

import pytest

import src.services.rule_monitor_runtime as runtime_module
from src.services.rule_monitor_fetch import DocumentError
from src.services.rule_monitor_history import RuleMonitorHistory
from src.services.rule_monitor_registry import sources
from tests.test_rule_monitor_runtime import setup, IDENTITY, URL


def expanded_monitor(tmp_path, count):
    monitor, calls = setup(tmp_path)
    source = monitor.registry()[IDENTITY]
    documents = tuple({"id": str(i), "title": "受控文档", "url": URL + str(i)} for i in range(count))
    monitor.registry = lambda: {IDENTITY: replace(source, documents=documents + documents[:1])}
    return monitor, calls, [item["url"] for item in documents]


def test_all_current_registered_sources_execute_with_controlled_documents(tmp_path):
    monitor, calls = setup(tmp_path)
    registry = sources()
    monitor.registry = lambda: registry
    expected = {doc["url"] for source in registry.values() for doc in source.documents}
    identity = str(uuid4())
    monitor.enqueue(identity, list(registry))
    assert monitor.run_pending_once()
    assert monitor.store.job(identity)["status"] == "succeeded"
    assert monitor.store.job(identity)["counts"]["documents"] == len(expected)
    assert set(calls) == expected and len(calls) == len(expected)
    assert monitor.store.job(identity)["sourceIds"] == list(registry)


@pytest.mark.parametrize("count", [29, 53])
def test_growing_catalog_releases_document_results_and_preserves_all_failures(tmp_path, count):
    monitor, _, urls = expanded_monitor(tmp_path, count)
    references = []

    class Result(dict):
        pass

    async def fetch(url, **kwargs):
        assert not references or references[-1]() is None
        result = Result(contentHash="b" * 64, rawHash="c" * 64)
        references.append(weakref.ref(result))
        return result

    monitor.fetch = fetch
    identity = str(uuid4())
    monitor.enqueue(identity, [IDENTITY])
    assert monitor.run_pending_once()
    assert monitor.store.job(identity)["status"] == "succeeded"
    assert len(references) == len(urls)
    assert all(reference() is None for reference in references)

    async def fail(url, **kwargs):
        raise DocumentError("document_http_error")

    monitor.fetch = fail
    failed = str(uuid4())
    monitor.enqueue(failed, [IDENTITY])
    assert monitor.run_pending_once()
    entry = RuleMonitorHistory(monitor.store).read()["items"][0]
    assert entry["id"] == failed and entry["status"] == "partial"
    assert entry["counts"]["unavailable"] == len(urls)
    assert {item["identity"] for item in entry["failures"]} == set(urls)


def test_run_deadline_prevents_further_reads_and_records_remaining_failures(tmp_path, monkeypatch):
    monitor, calls, urls = expanded_monitor(tmp_path, 53)
    now = [0]
    monkeypatch.setattr(runtime_module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def fetch(url, **kwargs):
        assert kwargs["budget_seconds"] == 300
        calls.append(url)
        now[0] = 301
        return {"contentHash": "b" * 64, "rawHash": "c" * 64}

    monitor.fetch = fetch
    identity = str(uuid4())
    monitor.enqueue(identity, [IDENTITY])
    assert monitor.run_pending_once()
    entry = RuleMonitorHistory(monitor.store).read()["items"][0]
    assert len(calls) == 1 and entry["status"] == "partial"
    assert len(entry["failures"]) == len(urls) - 1
    assert all(item["errorCode"] == "run_budget_exceeded" for item in entry["failures"])


def test_fetch_is_cancelled_at_deadline_without_retries(tmp_path):
    monitor, calls, urls = expanded_monitor(tmp_path, 29)
    cancelled = []

    async def fetch(url, **kwargs):
        calls.append(url)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(url)

    monitor.fetch = fetch
    identity = str(uuid4())
    monitor.enqueue(identity, [IDENTITY])
    job = monitor.store.claim("owner")
    asyncio.run(monitor._execute(job, "owner", budget_seconds=0.01))
    assert calls == cancelled == [urls[0]]
    assert monitor.store.job(identity)["status"] == "partial"
    assert monitor.store.job(identity)["counts"]["unavailable"] == len(urls)


def test_stop_prevents_reads_and_inline_capture_keeps_existing_return_contract(tmp_path):
    monitor, calls, urls = expanded_monitor(tmp_path, 29)
    identity = str(uuid4())
    monitor.enqueue(identity, [IDENTITY])
    monitor.halt.set()
    assert monitor.run_pending_once()
    assert calls == [] and monitor.store.job(identity)["status"] == "interrupted"
    monitor.halt.clear()
    identity, captured = monitor.inspect_once(IDENTITY)
    assert set(captured) == set(urls)
    assert monitor.store.job(identity)["status"] == "succeeded"
