"""资料版本和最后成功摘要；失败不覆盖，晚到执行不提交。"""

from src.services.rule_monitor_store import instant


class RuleMonitorObservations:
    def __init__(self, store):
        self.store = store

    def record(self, job, owner, *, kind, identity, name, digest=None, raw_digest=None, dataset=None, error=None):
        with self.store.connection(True) as db:
            active = db.execute("SELECT 1 FROM runs WHERE id=? AND status='running' AND attempt=1 "
                "AND owner=? AND lease>?", (job, owner, self.store.clock())).fetchone()
            if active is None:
                return False
            before = db.execute("SELECT * FROM latest WHERE kind=? AND identity=?", (kind, identity)).fetchone()
            now = instant(self.store.clock())
            old_hash = before["digest"] if before else None
            status = "unavailable" if error else "baseline"
            if not error and old_hash is not None:
                status = "unchanged" if old_hash == digest else "changed"
            last_change = None
            succeeded = None
            if before is not None:
                last_change = before["changed"]
                succeeded = before["succeeded"]
            if status == "changed":
                last_change = now
            if not error:
                succeeded = now
            if error:
                digest = old_hash
                raw_digest = before["raw_digest"] if before else None
                dataset = before["dataset"] if before else None
            else:
                db.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,identity,digest) "
                    "DO UPDATE SET last_seen=excluded.last_seen", (kind, identity, digest, raw_digest, dataset, now, now))
            db.execute("INSERT INTO latest VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(kind,identity) DO UPDATE SET "
                "name=excluded.name,digest=excluded.digest,raw_digest=excluded.raw_digest,dataset=excluded.dataset,"
                "checked=excluded.checked,succeeded=excluded.succeeded,changed=excluded.changed,status=excluded.status,error=excluded.error",
                (kind, identity, name, digest, raw_digest, dataset, now, succeeded, last_change, status, error))
            db.execute("UPDATE runs SET documents=documents+?,changed=changed+?,unavailable=unavailable+? WHERE id=?",
                (int(kind == "document"), int(status == "changed"), int(error is not None), job))
            return True

    def status(self):
        with self.store.connection() as db:
            rows = db.execute("SELECT latest.*,COUNT(versions.digest) AS version_count FROM latest LEFT JOIN versions "
                "ON latest.kind=versions.kind AND latest.identity=versions.identity GROUP BY latest.kind,latest.identity "
                "ORDER BY latest.checked DESC,latest.identity LIMIT 64").fetchall()
            return [{"kind": row["kind"], "identity": row["identity"], "name": row["name"],
                "contentHash": row["digest"], "rawHash": row["raw_digest"], "datasetId": row["dataset"],
                "lastCheckedAt": row["checked"], "lastSuccessAt": row["succeeded"], "lastChangeAt": row["changed"],
                "status": row["status"], "errorCode": row["error"], "versionCount": row["version_count"]} for row in rows]

    def versions(self, kind, identity):
        with self.store.connection() as db:
            rows = db.execute("SELECT * FROM versions WHERE kind=? AND identity=? ORDER BY last_seen DESC LIMIT 20",
                (kind, identity)).fetchall()
            return [{"contentHash": row["digest"], "rawHash": row["raw_digest"], "datasetId": row["dataset"],
                "firstSeenAt": row["first_seen"], "lastSeenAt": row["last_seen"]} for row in rows]
