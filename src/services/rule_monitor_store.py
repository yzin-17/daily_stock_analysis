"""收费监测配置与任务的独立SQLite事实源。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time
from src.services.rule_monitor_schedule import DEFAULT_SCHEDULE, DEFAULT_TIMEZONE, next_occurrence


def instant(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class MonitorConflict(ValueError):
    pass


class RuleMonitorStore:
    def __init__(self, path, *, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2, 3, 4):
                raise ValueError("监测库结构版本不受支持")
            db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS config (id INTEGER PRIMARY KEY CHECK(id=1),
                    revision INTEGER NOT NULL, enabled INTEGER NOT NULL, sources TEXT NOT NULL);
                INSERT OR IGNORE INTO config VALUES(1,0,0,'[]');
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0, owner TEXT, lease REAL, sources TEXT NOT NULL,
                    created REAL NOT NULL, started REAL, finished REAL, documents INTEGER NOT NULL DEFAULT 0,
                    changed INTEGER NOT NULL DEFAULT 0, unavailable INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE TABLE IF NOT EXISTS versions (kind TEXT NOT NULL, identity TEXT NOT NULL,
                    digest TEXT NOT NULL, raw_digest TEXT, dataset TEXT, first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL, PRIMARY KEY(kind,identity,digest));
                CREATE TABLE IF NOT EXISTS latest (kind TEXT NOT NULL, identity TEXT NOT NULL,
                    name TEXT NOT NULL, digest TEXT, raw_digest TEXT, dataset TEXT, checked TEXT NOT NULL,
                    succeeded TEXT, changed TEXT, status TEXT NOT NULL, error TEXT,
                    PRIMARY KEY(kind,identity));
                CREATE TABLE IF NOT EXISTS run_metadata (job_id TEXT PRIMARY KEY,
                    trigger TEXT NOT NULL CHECK(trigger IN ('manual','scheduled')));
                CREATE TABLE IF NOT EXISTS run_failures (job_id TEXT NOT NULL, kind TEXT NOT NULL,
                    identity TEXT NOT NULL, name TEXT NOT NULL, error TEXT NOT NULL,
                    PRIMARY KEY(job_id,kind,identity));
                CREATE INDEX IF NOT EXISTS runs_history ON runs(created DESC,id DESC);
                CREATE TABLE IF NOT EXISTS schedule (id INTEGER PRIMARY KEY CHECK(id=1),
                    cron TEXT NOT NULL, timezone TEXT NOT NULL, next_due REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS version_snapshots (kind TEXT NOT NULL, identity TEXT NOT NULL,
                    digest TEXT NOT NULL, format TEXT, body TEXT, reason TEXT, captured TEXT NOT NULL,
                    PRIMARY KEY(kind,identity,digest));
                PRAGMA user_version=4;
            """)
            previous = db.execute("SELECT sources FROM config WHERE id=1").fetchone()
            if json.loads(previous["sources"]):
                last = db.execute("SELECT MAX(created) FROM runs").fetchone()[0]
                due = (last if last is not None else self.clock()) + 86400
                db.execute("INSERT OR IGNORE INTO schedule VALUES(1,?,?,?)",
                    (DEFAULT_SCHEDULE, DEFAULT_TIMEZONE, due))

    @contextmanager
    def connection(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _config(db):
        row = db.execute("SELECT * FROM config WHERE id=1").fetchone()
        schedule = db.execute("SELECT * FROM schedule WHERE id=1").fetchone()
        return {"revision": row["revision"], "enabled": bool(row["enabled"]),
            "sourceIds": json.loads(row["sources"]), "taskCreated": schedule is not None,
            "cron": schedule["cron"] if schedule else DEFAULT_SCHEDULE,
            "timezone": schedule["timezone"] if schedule else DEFAULT_TIMEZONE,
            "nextRunAt": instant(schedule["next_due"]) if schedule and row["enabled"] else None}

    def config(self):
        with self.connection() as db:
            db.execute("BEGIN")
            return self._config(db)

    def configure(self, expected_revision, enabled, source_ids):
        with self.connection(True) as db:
            previous = self._config(db)
            changed = db.execute("UPDATE config SET revision=revision+1,enabled=?,sources=? WHERE id=1 AND revision=?",
                (int(enabled), json.dumps(source_ids), expected_revision)).rowcount
            if changed != 1:
                raise MonitorConflict("config_revision_conflict")
            if source_ids:
                db.execute("INSERT OR IGNORE INTO schedule VALUES(1,?,?,?)",
                    (DEFAULT_SCHEDULE, DEFAULT_TIMEZONE, self.clock() + 86400))
            schedule = db.execute("SELECT * FROM schedule WHERE id=1").fetchone()
            if enabled and not previous["enabled"] and schedule and schedule["next_due"] <= self.clock():
                db.execute("UPDATE schedule SET next_due=? WHERE id=1",
                    (next_occurrence(schedule["cron"], schedule["timezone"], self.clock()),))
            return self._config(db)

    def create_task(self, expected_revision, source_ids):
        if not source_ids:
            raise ValueError("创建任务需选择来源")
        with self.connection(True) as db:
            exists = db.execute("SELECT 1 FROM schedule WHERE id=1").fetchone() is not None
            changed = db.execute("UPDATE config SET revision=revision+1,sources=?,enabled=? WHERE id=1 AND revision=?",
                (json.dumps(source_ids), self._config(db)["enabled"] if exists else 1, expected_revision)).rowcount
            if changed != 1:
                raise MonitorConflict("config_revision_conflict")
            db.execute("INSERT OR IGNORE INTO schedule VALUES(1,?,?,?)",
                (DEFAULT_SCHEDULE, DEFAULT_TIMEZONE, self.clock() + 86400))
            return self._config(db)

    def set_schedule(self, expected_revision, cron, timezone):
        with self.connection(True) as db:
            due = next_occurrence(cron, timezone, self.clock())
            if not db.execute("SELECT 1 FROM schedule WHERE id=1").fetchone():
                raise MonitorConflict("task_not_created")
            changed = db.execute("UPDATE config SET revision=revision+1 WHERE id=1 AND revision=?",
                (expected_revision,)).rowcount
            if changed != 1:
                raise MonitorConflict("config_revision_conflict")
            db.execute("UPDATE schedule SET cron=?,timezone=?,next_due=? WHERE id=1", (cron, timezone, due))
            return self._config(db)

    @staticmethod
    def _public(row):
        if row is None:
            return None
        return {"id": row["id"], "status": row["status"], "attempt": row["attempt"],
            "sourceIds": json.loads(row["sources"]), "createdAt": instant(row["created"]),
            "startedAt": instant(row["started"]) if row["started"] is not None else None,
            "finishedAt": instant(row["finished"]) if row["finished"] is not None else None,
            "counts": {key: row[key] for key in ("documents", "changed", "unavailable")}, "errorCode": row["error"]}

    def enqueue(self, identity, source_ids):
        with self.connection(True) as db:
            existing = db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone()
            if existing is not None:
                if json.loads(existing["sources"]) != source_ids:
                    raise MonitorConflict("request_identity_conflict")
                return self._public(existing)
            if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone():
                raise MonitorConflict("monitor_busy")
            db.execute("INSERT INTO runs(id,status,sources,created) VALUES(?,'queued',?,?)",
                (identity, json.dumps(source_ids), self.clock()))
            db.execute("INSERT INTO run_metadata VALUES(?, 'manual')", (identity,))
            return self._public(db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone())

    def schedule_due(self, identity):
        with self.connection(True) as db:
            config = self._config(db)
            if not config["enabled"] or not config["sourceIds"]:
                return False
            schedule = db.execute("SELECT * FROM schedule WHERE id=1").fetchone()
            if schedule is None or schedule["next_due"] > self.clock():
                return False
            if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone():
                return False
            db.execute("INSERT INTO runs(id,status,sources,created) VALUES(?,'queued',?,?)",
                (identity, json.dumps(config["sourceIds"]), self.clock()))
            db.execute("INSERT INTO run_metadata VALUES(?, 'scheduled')", (identity,))
            db.execute("UPDATE schedule SET next_due=? WHERE id=1",
                (next_occurrence(schedule["cron"], schedule["timezone"], self.clock()),))
            return True

    def claim(self, owner):
        with self.connection(True) as db:
            now = self.clock()
            db.execute("UPDATE runs SET status='interrupted',finished=?,error='lease_expired' "
                "WHERE status='running' AND lease<=?", (now, now))
            if db.execute("SELECT 1 FROM runs WHERE status='running'").fetchone():
                return None
            row = db.execute("SELECT * FROM runs WHERE status='queued' AND attempt=0 ORDER BY created,id LIMIT 1").fetchone()
            if row is None:
                return None
            changed = db.execute("UPDATE runs SET status='running',attempt=1,owner=?,lease=?,started=? "
                "WHERE id=? AND status='queued' AND attempt=0", (owner, now + 360, now, row["id"])).rowcount
            if changed != 1:
                return None
            return self._public(db.execute("SELECT * FROM runs WHERE id=?", (row["id"],)).fetchone())

    def start_inline(self, identity, owner, source_ids):
        """按需读取原子领取自己的检查，不执行或抢占其他排队任务。"""
        with self.connection(True) as db:
            now = self.clock()
            db.execute("UPDATE runs SET status='interrupted',finished=?,error='lease_expired' "
                "WHERE status='running' AND lease<=?", (now, now))
            if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running')").fetchone():
                return None
            db.execute("INSERT INTO runs(id,status,attempt,owner,lease,sources,created,started) "
                "VALUES(?,'running',1,?,?,?,?,?)", (identity, owner, now + 360, json.dumps(source_ids), now, now))
            db.execute("INSERT INTO run_metadata VALUES(?, 'manual')", (identity,))
            return self._public(db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone())

    def finish(self, identity, owner, error=None, interrupted=False):
        with self.connection(True) as db:
            return db.execute("UPDATE runs SET status=CASE WHEN ? THEN 'interrupted' WHEN ? IS NOT NULL THEN 'failed' "
                "WHEN unavailable>0 THEN 'partial' ELSE 'succeeded' END,finished=?,error=? "
                "WHERE id=? AND status='running' AND attempt=1 AND owner=? AND lease>?",
                (int(interrupted), error, self.clock(), error, identity, owner, self.clock())).rowcount == 1

    def job(self, identity=None):
        with self.connection() as db:
            if identity is None:
                row = db.execute("SELECT * FROM runs ORDER BY created DESC,id DESC LIMIT 1").fetchone()
            else:
                row = db.execute("SELECT * FROM runs WHERE id=?", (identity,)).fetchone()
            return self._public(row)
