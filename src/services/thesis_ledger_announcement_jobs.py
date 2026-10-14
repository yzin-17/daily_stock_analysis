"""公告获取的持久状态；文件锁串行领取，执行标识隔离晚到提交。"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from hashlib import sha256
import calendar
import fcntl
import json
from pathlib import Path
import re
import tempfile
import time
import uuid
from zoneinfo import ZoneInfo
from src.services.thesis_ledger_announcement_parser import PARSER_REVISION


def month_windows(start, end, today=None):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last or (last - first).days > 366 * 30:
        raise ValueError("公告准备区间须为三十年以内的有效日期")
    today = today or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    last = min(last, today)
    current = first.replace(day=1)
    while current <= last:
        finish = current.replace(day=calendar.monthrange(current.year, current.month)[1])
        yield current.isoformat(), min(finish, today).isoformat()
        current = finish + timedelta(days=1)


class AnnouncementJobs:
    REVISION = f"cninfo-company-v1:{PARSER_REVISION}"
    LEASE_SECONDS = 300
    MAX_ATTEMPTS = 3

    def __init__(self, root, clock=time.time):
        self.root = Path(root) / ".acquisition"
        self.clock = clock

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "lock").open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def read(self, identity):
        return json.loads((self.root / f"{identity}.json").read_text())

    def write(self, identity, value):
        with tempfile.NamedTemporaryFile(mode="w", dir=self.root, delete=False, encoding="utf-8") as stream:
            path = Path(stream.name)
            json.dump(value, stream, ensure_ascii=False, sort_keys=True)
        try:
            path.replace(self.root / f"{identity}.json")
        finally:
            path.unlink(missing_ok=True)

    def ensure(self, symbols, start, end, *, used=True, refresh=False):
        if not 1 <= len(symbols) <= 5 or any(not re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", s) for s in symbols):
            raise ValueError("公告准备须提供一至五个规范证券代码")
        ids, now = [], self.clock()
        today = datetime.fromtimestamp(now, ZoneInfo("Asia/Shanghai")).date()
        windows = list(month_windows(start, end, today))
        with self.locked():
            usage_path = self.root / "usage.json"
            usage = json.loads(usage_path.read_text()) if usage_path.exists() else {}
            for symbol in sorted(set(symbols)):
                if used:
                    usage[symbol] = now
                for first, last in windows:
                    identity = sha256(f"{symbol}:{first}".encode()).hexdigest()
                    ids.append(identity)
                    path = self.root / f"{identity}.json"
                    old = self.read(identity) if path.exists() else None
                    recent = date.fromisoformat(last) >= today - timedelta(days=7)
                    ttl = 21600 if recent or (old and old["state"] == "failed") else float("inf")
                    expired = old and now - old.get("finishedAt", now) >= ttl
                    renew = old and old["state"] in ("succeeded", "failed") and (
                        expired or old["end"] != last or old.get("revision") != self.REVISION
                        or (refresh and now - old.get("finishedAt", now) >= 21600))
                    if old is None or renew:
                        self.write(identity, {"id": identity, "symbol": symbol, "start": first, "end": last,
                                              "state": "queued", "attempt": 0, "createdAt": now, "nextAt": now,
                                              "revision": self.REVISION})
            if used:
                self.write("usage", usage)
        return self.summary(ids)

    def summary(self, ids):
        with self.locked():
            rows = [self.read(identity) for identity in ids]
        terminal = [row for row in rows if row["state"] in ("succeeded", "failed")]
        errors = [error for row in terminal for error in row.get("result", {}).get("errors", [])]
        return {"status": "ready" if len(terminal) == len(rows) else "preparing", "jobIds": ids,
                "total": len(rows), "completed": len(terminal),
                "failed": sum(row["state"] == "failed" for row in terminal),
                "matched": sum(row.get("result", {}).get("matched", 0) for row in terminal),
                "downloaded": sum(row.get("result", {}).get("downloaded", 0) for row in terminal),
                "parsed": sum(row.get("result", {}).get("parsed", 0) for row in terminal),
                "errors": errors[:20]}

    def claim(self):
        now = self.clock()
        with self.locked():
            for path in sorted(self.root.glob("*.json")):
                if path.stem in ("usage", "schedule"):
                    continue
                row = self.read(path.stem)
                if row["state"] == "running" and row["leaseUntil"] <= now:
                    row["state"] = "queued"
                if row["state"] != "queued" or row["nextAt"] > now:
                    continue
                if row["attempt"] >= self.MAX_ATTEMPTS:
                    row.update(state="failed", finishedAt=now,
                               result={"errors": [{"stage": "execution", "reason": "公告获取重试耗尽"}]})
                    self.write(row["id"], row)
                    continue
                row.update(state="running", attempt=row["attempt"] + 1, owner=uuid.uuid4().hex,
                           leaseUntil=now + self.LEASE_SECONDS)
                self.write(row["id"], row)
                return row
        return None

    def finish(self, row, result):
        now = self.clock()
        with self.locked():
            current = self.read(row["id"])
            if current["state"] != "running" or current["owner"] != row["owner"] or current["leaseUntil"] <= now:
                return False
            failed = bool(result.get("errors") or result.get("truncated"))
            current.update(result=result)
            if failed and current["attempt"] < self.MAX_ATTEMPTS:
                current.update(state="queued", nextAt=now + 30 * current["attempt"])
            else:
                current.update(state="failed" if failed else "succeeded", finishedAt=now)
            self.write(row["id"], current)
            return True

    def recent_symbols(self):
        with self.locked():
            path = self.root / "usage.json"
            usage = json.loads(path.read_text()) if path.exists() else {}
        return [s for s, used_at in usage.items() if self.clock() - used_at <= 30 * 86400]
