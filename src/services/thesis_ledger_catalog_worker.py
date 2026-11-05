"""目录任务的进程内调度与显式退出；持久状态由注入仓储拥有。"""

import logging
import os
import queue
from threading import Event, Lock, Thread
from typing import Callable
import uuid

from src.services.thesis_ledger_catalog_execution import CatalogExecutionStopped, catalog_execution

logger = logging.getLogger(__name__)


class CatalogJobWorker:
    def __init__(self, database_path: str, store_factory: Callable):
        self.database_path = database_path
        self.owner = f"catalog-worker:pid:{os.getpid()}:{uuid.uuid4().hex}"
        self.stopping = Event()
        self._lock = Lock()
        self._queue = queue.Queue()
        self.store = store_factory(database_path)
        for job_id, owner in self.store.recover_catalog_jobs(self.owner):
            self.enqueue(job_id, owner)
        self._thread = Thread(target=self._run, name="thesis-ledger-catalog-worker", daemon=True)
        self._thread.start()

    def enqueue(self, job_id: str, owner: str) -> None:
        with self._lock:
            if self.stopping.is_set():
                raise RuntimeError("目录执行器正在停止，持久任务将在下次启动恢复")
            self._queue.put((job_id, owner))

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                if self.stopping.is_set():
                    continue
                job_id, owner = item
                try:
                    with catalog_execution(self.stopping):
                        self.store.execute_catalog_job(job_id, owner)
                except CatalogExecutionStopped:
                    try:
                        self.store.release_catalog_job_on_stop(job_id, owner)
                    except Exception:
                        logger.error("目录停止状态未能保存，将按持久租约恢复")
                except Exception:
                    logger.error("目录执行器任务未正常完成，等待持久状态恢复")
            finally:
                self._queue.task_done()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if not self.stopping.is_set():
                self.stopping.set()
                self._queue.put(None)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            raise RuntimeError("目录执行器未在退出期限内结束")


catalog_job_workers: dict[str, CatalogJobWorker] = {}
catalog_job_workers_lock = Lock()


def catalog_worker_for(database_path: str, store_factory: Callable) -> CatalogJobWorker:
    with catalog_job_workers_lock:
        worker = catalog_job_workers.get(database_path)
        if worker is None:
            worker = CatalogJobWorker(database_path, store_factory)
            catalog_job_workers[database_path] = worker
        elif worker.stopping.is_set():
            raise RuntimeError("目录执行器正在停止")
        return worker


def stop_catalog_worker(database_path: str, timeout: float = 5.0) -> None:
    with catalog_job_workers_lock:
        worker = catalog_job_workers.get(database_path)
    if worker is None:
        return
    worker.stop(timeout)
    with catalog_job_workers_lock:
        if catalog_job_workers.get(database_path) is worker:
            del catalog_job_workers[database_path]
