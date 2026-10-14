"""生命周期内执行公告任务与每日增量更新；网络适配器由装配层注入。"""
from datetime import datetime, timedelta
import logging
from threading import Event, Thread
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


class AnnouncementRuntime:
    def __init__(self, jobs, acquire, allowed):
        self.jobs, self.acquire, self.allowed = jobs, acquire, allowed
        self.stopping = Event()
        self.threads = []

    def start(self):
        for name, function in (("announcement-fetch", self.work), ("announcement-schedule", self.schedule)):
            thread = Thread(target=function, name=name, daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self):
        self.stopping.set()
        for thread in self.threads:
            thread.join(timeout=1)

    def prepare(self, symbols, start, end):
        if not self.allowed():
            return {"status": "ready", "jobIds": [], "total": 0, "completed": 0, "failed": 0,
                    "matched": 0, "downloaded": 0, "parsed": 0, "errors": []}
        return self.jobs.ensure(symbols, start, end)

    def execute_one(self):
        if not self.allowed():
            return False
        row = self.jobs.claim()
        if row is None:
            return False
        try:
            result = self.acquire([row["symbol"]], row["start"], row["end"], max_pages=5, parse=True, budget_seconds=180)
        except Exception as exc:
            logger.exception("公告获取任务失败")
            result = {"errors": [{"stage": "execution", "reason": str(exc)}]}
        self.jobs.finish(row, result)
        return True

    def work(self):
        while not self.stopping.is_set():
            try:
                if self.execute_one():
                    continue
            except Exception:
                logger.exception("公告任务读取失败，稍后重试")
            self.stopping.wait(2)

    def schedule_once(self):
        now = datetime.fromtimestamp(self.jobs.clock(), ZoneInfo("Asia/Shanghai"))
        if now.hour < 22 or not self.allowed():
            return False
        identity = now.date().isoformat()
        with self.jobs.locked():
            path = self.jobs.root / "schedule.json"
            if path.exists() and self.jobs.read("schedule").get("day") == identity:
                return False
        end = now.date()
        for symbol in self.jobs.recent_symbols():
            self.jobs.ensure([symbol], (end - timedelta(days=7)).isoformat(), end.isoformat(), used=False, refresh=True)
        with self.jobs.locked():
            self.jobs.write("schedule", {"day": identity})
        return True

    def schedule(self):
        while not self.stopping.is_set():
            try:
                self.schedule_once()
            except Exception:
                logger.exception("公告每日更新失败，稍后重试")
            self.stopping.wait(60)
