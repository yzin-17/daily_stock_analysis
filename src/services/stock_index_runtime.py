"""股票索引刷新生命周期；停止等待实际下载执行单元回收。"""

import asyncio
from functools import partial
import logging
from threading import Event

from src.services.stock_index_process import (
    StockIndexRefreshStopped, isolated_stock_index_download, stock_index_download_worker,
)
from src.services.stock_index_remote_service import RemoteStockIndexSettings, refresh_remote_stock_index_cache

logger = logging.getLogger(__name__)


class StockIndexRuntime:
    def __init__(self, settings: RemoteStockIndexSettings, *, worker=stock_index_download_worker):
        self.settings = settings
        self.worker = worker
        self.stopping = Event()
        self.task: asyncio.Task | None = None
        self.started = False
        self._stop_lock = asyncio.Lock()

    def start(self) -> None:
        if self.started:
            return
        self.stopping.clear()
        self.started = True
        self.schedule("startup")

    def schedule(self, reason: str) -> bool:
        if not self.started or self.stopping.is_set() or not self.settings.enabled:
            return False
        if self.task is not None and not self.task.done():
            return False
        self.task = asyncio.create_task(self._refresh(reason), name="stock-index-refresh")
        return True

    async def _refresh(self, reason: str) -> None:
        download = partial(isolated_stock_index_download, stopping=self.stopping, worker=self.worker)
        future = asyncio.create_task(asyncio.to_thread(refresh_remote_stock_index_cache, self.settings, download=download))
        try:
            result = await asyncio.shield(future)
            if result.refreshed:
                logger.info("股票索引刷新完成，触发=%s", reason)
        except StockIndexRefreshStopped:
            pass
        except asyncio.CancelledError:
            self.stopping.set()
            try:
                await asyncio.shield(future)
            except StockIndexRefreshStopped:
                pass
            raise
        except Exception as exc:
            logger.warning("股票索引刷新执行失败，类型=%s", type(exc).__name__)

    async def stop(self) -> None:
        async with self._stop_lock:
            self.stopping.set()
            if self.task is not None:
                try:
                    await asyncio.shield(self.task)
                except asyncio.CancelledError:
                    if not self.task.cancelled():
                        await asyncio.shield(self.task)
                        raise
            self.started = False
