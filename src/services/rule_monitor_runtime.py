"""明确开关的收费资料监测工作线程；任务和版本均以SQLite为准。"""

import asyncio
import logging
from pathlib import Path
import threading
import time
import uuid

from src.services.rule_monitor_fetch import DocumentError, fetch_document
from src.services.rule_monitor_observations import RuleMonitorObservations
from src.services.rule_monitor_registry import sources
from src.services.rule_monitor_store import RuleMonitorStore

logger = logging.getLogger(__name__)
VERSION = "rule-source-monitor-v1"


class RuleMonitorRuntime:
    def __init__(self, store, *, registry=sources, fetch=fetch_document):
        self.store = store
        self.observations = RuleMonitorObservations(self.store)
        self.registry, self.fetch = registry, fetch
        self.halt, self.wake = threading.Event(), threading.Event()
        self.thread = None

    def status(self):
        return {"version": VERSION, "config": self.store.config(),
            "sources": [source.public() for source in self.registry().values()],
            "latestRun": self.store.job(), "observations": self.observations.status()}

    def validate_sources(self, identities, *, allow_empty=False):
        if (not identities and not allow_empty) or len(identities) > 9 or len(set(identities)) != len(identities):
            raise ValueError("监测来源范围无效")
        if any(identity not in self.registry() for identity in identities):
            raise ValueError("监测来源未登记")

    def enqueue(self, identity, identities):
        self.validate_sources(identities)
        result = self.store.enqueue(identity, identities)
        self.wake.set()
        return result

    async def _execute(self, job, owner):
        registry = self.registry()
        selected = [registry[identity] for identity in job["sourceIds"]]
        documents = {document["url"]: document["title"] for source in selected for document in source.documents}
        if len(documents) > 24:
            raise ValueError("监测文档超过预算")
        for source in selected:
            if not self.observations.record(job["id"], owner, kind="pack", identity=source.identity,
                    name=source.name, digest=source.digest, raw_digest=source.digest, dataset=source.dataset):
                return
        deadline = time.monotonic() + 300
        for url, title in documents.items():
            if self.halt.is_set():
                self.store.finish(job["id"], owner, "monitor_stopped", interrupted=True)
                return
            remaining = deadline - time.monotonic()
            result = None
            error = "run_budget_exceeded"
            if remaining > 0:
                try:
                    result = await self.fetch(url, budget_seconds=remaining)
                    error = None
                except DocumentError as failure:
                    error = failure.code
            if not self.observations.record(job["id"], owner, kind="document", identity=url, name=title,
                    digest=result["contentHash"] if result else None,
                    raw_digest=result["rawHash"] if result else None, error=error):
                return
        self.store.finish(job["id"], owner)

    def run_pending_once(self):
        owner = str(uuid.uuid4())
        job = self.store.claim(owner)
        if job is None:
            return False
        try:
            asyncio.run(self._execute(job, owner))
        except Exception:
            self.store.finish(job["id"], owner, "monitor_execution_failed")
            logger.warning("收费资料监测执行失败，任务=%s", job["id"])
        return True

    def _loop(self):
        while not self.halt.is_set():
            try:
                self.store.schedule_due(str(uuid.uuid4()))
                self.run_pending_once()
            except Exception:
                logger.warning("收费资料监测暂不可用，保留数据库状态")
            self.wake.wait(30)
            self.wake.clear()

    def start(self):
        if self.thread is not None:
            raise RuntimeError("监测运行时已启动")
        self.thread = threading.Thread(target=self._loop, name="rule-source-monitor", daemon=True)
        self.thread.start()

    def stop(self):
        self.halt.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(30)
            if self.thread.is_alive():
                raise RuntimeError("监测运行时未在停止窗口内结束")


def create_rule_monitor_runtime(database_path):
    path = Path(database_path).resolve().with_name("rule-source-monitor.sqlite")
    return RuleMonitorRuntime(RuleMonitorStore(path))
