"""数据应用资源的成组启动、逆序释放和失败回收。"""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager

from api.thesis_ledger_announcement_runtime import create_announcement_runtime
from api.thesis_ledger_oauth import initialize_provider_oauth, shutdown_provider_oauth
from src.data_config import get_data_config
from src.services.stock_index_remote_service import settings_from_config
from src.services.stock_index_runtime import StockIndexRuntime
from src.services.thesis_ledger_catalog_worker import stop_catalog_worker
from src.services.thesis_ledger_control import _catalog_job_manager_for
from src.services.rule_monitor_runtime import create_rule_monitor_runtime


def _clear_data_state(app) -> None:
    for name in ("data_lifecycle_active", "stock_index_runtime", "schedule_stock_index_refresh",
                 "announcement_runtime", "catalog_worker", "rule_monitor"):
        if hasattr(app.state, name):
            delattr(app.state, name)


async def _stop_thread_runtime(runtime) -> None:
    await asyncio.to_thread(runtime.stop)


async def _stop_catalog(database_path: str) -> None:
    await asyncio.to_thread(stop_catalog_worker, database_path)


@asynccontextmanager
async def data_lifespan(app):
    if getattr(app.state, "data_lifecycle_active", False) or hasattr(app.state, "provider_oauth_manager"):
        raise RuntimeError("数据应用资源已有活动生命周期")
    async with AsyncExitStack() as resources:
        app.state.data_lifecycle_active = True
        resources.callback(_clear_data_state, app)
        config = get_data_config()

        index = StockIndexRuntime(settings_from_config(config))
        app.state.stock_index_runtime = index
        app.state.schedule_stock_index_refresh = index.schedule
        resources.push_async_callback(index.stop)
        index.start()

        announcements = create_announcement_runtime()
        app.state.announcement_runtime = announcements
        resources.push_async_callback(_stop_thread_runtime, announcements)
        await asyncio.to_thread(announcements.start)

        resources.push_async_callback(shutdown_provider_oauth, app)
        initialize_provider_oauth(app)

        resources.push_async_callback(_stop_catalog, config.database_path)
        app.state.catalog_worker = await asyncio.to_thread(_catalog_job_manager_for, config.database_path)
        monitor = create_rule_monitor_runtime(config.database_path)
        app.state.rule_monitor = monitor
        resources.push_async_callback(_stop_thread_runtime, monitor)
        await asyncio.to_thread(monitor.start)
        yield
