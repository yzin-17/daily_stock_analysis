"""数据资源组合的失败回收、重复进入和真实 OAuth 退出。"""

import asyncio
from types import SimpleNamespace
from threading import Event, Thread

from fastapi import FastAPI
import pytest

from api import data_lifespan as module
from api.data_app import create_app
from src.data_config import reset_data_config


@pytest.mark.parametrize("failure,expected_stops", [
    ("config", []),
    ("index-create", []),
    ("index-start", ["index"]),
    ("announcement-create", ["index"]),
    ("announcement-start", ["announcement", "index"]),
    ("oauth-start", ["oauth", "announcement", "index"]),
    ("catalog-start", ["catalog", "oauth", "announcement", "index"]),
    ("monitor-create", ["catalog", "oauth", "announcement", "index"]),
    ("monitor-start", ["monitor", "catalog", "oauth", "announcement", "index"]),
    ("body", ["monitor", "catalog", "oauth", "announcement", "index"]),
    ("none", ["monitor", "catalog", "oauth", "announcement", "index"]),
])
def test_startup_failure_and_body_failure_release_every_acquired_resource(monkeypatch, tmp_path, failure, expected_stops):
    stops = []
    app = FastAPI()

    def checkpoint(stage):
        if failure == stage:
            raise RuntimeError("controlled-" + stage)

    def config():
        checkpoint("config")
        return SimpleNamespace(database_path=str(tmp_path / "data.db"), stock_index_remote_update_enabled=False)

    class Index:
        def __init__(self, settings):
            checkpoint("index-create")

        def start(self):
            checkpoint("index-start")

        def schedule(self, reason):
            pass

        async def stop(self):
            stops.append("index")

    class Announcements:
        def start(self):
            checkpoint("announcement-start")

        def stop(self):
            stops.append("announcement")

    def announcements():
        checkpoint("announcement-create")
        return Announcements()

    def oauth_start(target):
        checkpoint("oauth-start")
        target.state.provider_oauth_manager = object()

    async def oauth_stop(target):
        stops.append("oauth")
        if hasattr(target.state, "provider_oauth_manager"):
            delattr(target.state, "provider_oauth_manager")

    def catalog_start(path):
        checkpoint("catalog-start")
        return object()

    class Monitor:
        def start(self):
            checkpoint("monitor-start")

        def stop(self):
            stops.append("monitor")

    def monitor_create(path):
        checkpoint("monitor-create")
        return Monitor()

    monkeypatch.setattr(module, "get_data_config", config)
    monkeypatch.setattr(module, "StockIndexRuntime", Index)
    monkeypatch.setattr(module, "create_announcement_runtime", announcements)
    monkeypatch.setattr(module, "initialize_provider_oauth", oauth_start)
    monkeypatch.setattr(module, "shutdown_provider_oauth", oauth_stop)
    monkeypatch.setattr(module, "_catalog_job_manager_for", catalog_start)
    monkeypatch.setattr(module, "stop_catalog_worker", lambda path: stops.append("catalog"))
    monkeypatch.setattr(module, "create_rule_monitor_runtime", monitor_create)

    async def run():
        try:
            async with module.data_lifespan(app):
                assert app.state.data_lifecycle_active
                checkpoint("body")
        except RuntimeError as error:
            assert str(error) == "controlled-" + failure
        else:
            assert failure == "none"
        assert not hasattr(app.state, "data_lifecycle_active")
        assert not hasattr(app.state, "provider_oauth_manager")
        assert not hasattr(app.state, "rule_monitor")
    asyncio.run(run())
    assert stops == expected_stops


def test_cleanup_failure_still_releases_later_resources(monkeypatch, tmp_path):
    stopped = []
    app = FastAPI()
    config = SimpleNamespace(database_path=str(tmp_path / "data.db"), stock_index_remote_update_enabled=False)

    class Index:
        def __init__(self, settings):
            pass

        def start(self):
            pass

        def schedule(self, reason):
            pass

        async def stop(self):
            stopped.append("index")

    class Announcements:
        def start(self):
            pass

        def stop(self):
            stopped.append("announcement")
            raise RuntimeError("controlled-stop-failure")

    monkeypatch.setattr(module, "get_data_config", lambda: config)
    monkeypatch.setattr(module, "StockIndexRuntime", Index)
    monkeypatch.setattr(module, "create_announcement_runtime", Announcements)
    monkeypatch.setattr(module, "initialize_provider_oauth", lambda app: None)
    monkeypatch.setattr(module, "shutdown_provider_oauth", lambda app: asyncio.sleep(0))
    monkeypatch.setattr(module, "_catalog_job_manager_for", lambda path: None)
    monkeypatch.setattr(module, "stop_catalog_worker", lambda path: stopped.append("catalog"))
    monkeypatch.setattr(module, "create_rule_monitor_runtime", lambda path: SimpleNamespace(
        start=lambda: None, stop=lambda: stopped.append("monitor")))

    async def run():
        with pytest.raises(RuntimeError, match="controlled-stop-failure"):
            async with module.data_lifespan(app):
                pass
        assert not hasattr(app.state, "data_lifecycle_active")
    asyncio.run(run())
    assert stopped == ["monitor", "catalog", "announcement", "index"]


def test_real_application_restarts_and_closes_pending_oauth_listener(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "data.db"))
    monkeypatch.setenv("THESIS_LEDGER_CONTROL_TOKEN", "fixture-control-token")
    monkeypatch.setenv("THESIS_LEDGER_DSA_SECRET_KEY", "fixture-secret-key")
    monkeypatch.setenv("STOCK_INDEX_REMOTE_UPDATE_ENABLED", "false")
    env = tmp_path / "empty.env"
    env.write_text("")
    monkeypatch.setenv("ENV_FILE", str(env))
    reset_data_config()
    released = []

    class Builder:
        def __init__(self, *args, **kwargs):
            pass

        async def build_async(self, open_url):
            open_url("https://openapi.longbridge.com/oauth2/authorize?state=fixture")
            try:
                await asyncio.Future()
            finally:
                released.append(True)

    app = create_app()

    async def run():
        for _ in range(2):
            async with app.router.lifespan_context(app):
                announcements = list(app.state.announcement_runtime.threads)
                worker = app.state.catalog_worker
                assert len(announcements) == 2 and worker._thread.is_alive()
                with pytest.raises(RuntimeError, match="活动生命周期"):
                    async with app.router.lifespan_context(app):
                        pass
                assert app.state.data_lifecycle_active and worker._thread.is_alive()
                manager = app.state.provider_oauth_manager
                manager.builder_factory = lambda: Builder
                session = await manager.create("fixture-client")
                deadline = asyncio.get_running_loop().time() + 3
                while manager.get(session["sessionId"])["status"] == "starting":
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0)
            assert not worker._thread.is_alive() and not any(thread.is_alive() for thread in announcements)
            assert manager.tasks == {} and not hasattr(app.state, "provider_oauth_manager")
            assert not hasattr(app.state, "data_lifecycle_active")
            assert manager.repository.get(session["sessionId"])["errorCode"] == "OAUTH_RESTARTED"
        assert released == [True, True]
    try:
        asyncio.run(run())
    finally:
        reset_data_config()


def test_cancellation_during_thread_start_waits_for_startup_then_releases(monkeypatch, tmp_path):
    from src.services import thesis_ledger_announcement_runtime as announcement_module
    from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs
    entered, release = Event(), Event()

    class SlowStartThread(Thread):
        def start(self):
            if self.name == "announcement-fetch":
                entered.set()
                assert release.wait(3)
            super().start()

    runtime = announcement_module.AnnouncementRuntime(AnnouncementJobs(tmp_path), lambda *a, **kw: {}, lambda: False)
    config = SimpleNamespace(database_path=str(tmp_path / "data.db"), stock_index_remote_update_enabled=False)
    monkeypatch.setattr(announcement_module, "Thread", SlowStartThread)
    monkeypatch.setattr(module, "get_data_config", lambda: config)
    monkeypatch.setattr(module, "create_announcement_runtime", lambda: runtime)
    app = FastAPI()

    async def enter():
        async with module.data_lifespan(app):
            pytest.fail("cancelled startup reached application body")

    async def run():
        task = asyncio.create_task(enter())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0.02)
            assert not task.done()
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert runtime.threads == []
            assert not hasattr(app.state, "data_lifecycle_active")
    asyncio.run(run())
