"""索引刷新真实 HTTP、父进程缓存和取消后的执行单元回收。"""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import multiprocessing
import os
from threading import Thread
import time

import pytest

from src.data import stock_index_loader
from src.services import stock_index_remote_service as service
from src.services.stock_index_runtime import StockIndexRuntime


def payload(name="受控银行"):
    return [[f"{i + 1:06d}.SZ", f"{i + 1:06d}", name, "controlled", "c", [], "CN", "stock", True, 100]
            for i in range(100)]


def blocked_download(connection, settings):
    try:
        time.sleep(30)
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def reset_index_state():
    service._reset_remote_stock_index_state_for_tests()
    stock_index_loader.clear_stock_index_cache()
    yield
    service._reset_remote_stock_index_state_for_tests()
    stock_index_loader.clear_stock_index_cache()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    state = {"calls": 0, "status": 200}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["calls"] += 1
            self.send_response(state["status"])
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload()).encode())

        def log_message(self, *_args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}/stocks.index.json", state
    finally:
        http.shutdown()
        http.server_close()
        thread.join(5)
        assert not thread.is_alive()


def test_real_refresh_updates_parent_cache_coalesces_requests_and_preserves_ttl(tmp_path, monkeypatch, server):
    url, state = server
    cache = tmp_path / "stocks.index.json"
    cache.write_text(json.dumps(payload("旧银行")))
    os.utime(cache, (1, 1))
    monkeypatch.setattr(stock_index_loader, "get_stock_index_candidate_paths", lambda: (cache,))
    monkeypatch.setattr(stock_index_loader, "get_remote_stock_index_cache_path", lambda: cache)
    assert stock_index_loader.get_index_stock_name("000001.SZ") == "旧银行"

    async def run():
        runtime = StockIndexRuntime(service.RemoteStockIndexSettings(url=url, cache_path=cache))
        try:
            runtime.start()
            assert runtime.schedule("concurrent-request") is False
            await asyncio.wait_for(asyncio.shield(runtime.task), 10)
            assert stock_index_loader.get_index_stock_name("000001.SZ") == "受控银行"
            assert state["calls"] == 1
            assert runtime.schedule("fresh-cache") is True
            await asyncio.wait_for(asyncio.shield(runtime.task), 10)
            assert state["calls"] == 1
        finally:
            await runtime.stop()
            await runtime.stop()
        assert runtime.schedule("after-stop") is False
        runtime.start()
        await asyncio.wait_for(asyncio.shield(runtime.task), 10)
        await runtime.stop()
        assert state["calls"] == 1
    asyncio.run(run())


@pytest.mark.parametrize("outer_cancel", [False, True])
def test_stop_or_outer_cancellation_reaps_actual_download_process(tmp_path, outer_cancel):
    baseline = {process.pid for process in multiprocessing.active_children()}
    cache = tmp_path / "stocks.index.json"

    async def run():
        runtime = StockIndexRuntime(service.RemoteStockIndexSettings(cache_path=cache), worker=blocked_download)
        child = None
        try:
            runtime.start()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                child = next((process for process in multiprocessing.active_children()
                              if process.pid not in baseline and process.name == "stock-index-download"), None)
                if child:
                    break
                await asyncio.sleep(0.01)
            assert child is not None
            child_pid = child.pid
            if outer_cancel:
                runtime.task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await runtime.task
            await runtime.stop()
            assert child_pid not in {process.pid for process in multiprocessing.active_children()}
            assert runtime.task.done() and not cache.exists()
            assert service._REMOTE_CONSECUTIVE_FAILURES == 0
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_three_real_transport_failures_preserve_parent_suppression_and_seed(tmp_path, server):
    url, state = server
    state["status"] = 503
    cache = tmp_path / "stocks.index.json"
    original = json.dumps(payload("旧银行")).encode()
    cache.write_bytes(original)
    os.utime(cache, (1, 1))

    async def run():
        runtime = StockIndexRuntime(service.RemoteStockIndexSettings(url=url, cache_path=cache))
        try:
            runtime.start()
            await asyncio.wait_for(asyncio.shield(runtime.task), 10)
            for _ in range(3):
                assert runtime.schedule("retry") is True
                await asyncio.wait_for(asyncio.shield(runtime.task), 10)
            assert state["calls"] == 3
            assert service._REMOTE_CONSECUTIVE_FAILURES == 3
            assert service._REMOTE_SUPPRESS_UNTIL > time.time()
            assert cache.read_bytes() == original
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_disabled_runtime_does_not_create_execution_task(tmp_path):
    async def run():
        runtime = StockIndexRuntime(service.RemoteStockIndexSettings(enabled=False, cache_path=tmp_path / "cache"))
        runtime.start()
        assert runtime.task is None and runtime.schedule("request") is False
        await runtime.stop()
    asyncio.run(run())


def test_execution_start_failure_does_not_leave_process_or_write_cache(tmp_path):
    baseline = {process.pid for process in multiprocessing.active_children()}
    cache = tmp_path / "stocks.index.json"

    async def run():
        runtime = StockIndexRuntime(service.RemoteStockIndexSettings(cache_path=cache),
                                    worker=lambda *_args: None)
        try:
            runtime.start()
            await asyncio.wait_for(asyncio.shield(runtime.task), 10)
        finally:
            await runtime.stop()
        assert not cache.exists()
        assert {process.pid for process in multiprocessing.active_children()} == baseline
        assert service._REMOTE_CONSECUTIVE_FAILURES == 1
    asyncio.run(run())
