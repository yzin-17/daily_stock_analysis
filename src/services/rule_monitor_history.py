"""逐次监测执行的只读分页投影；不以当前观察覆盖过去失败。"""


class RuleMonitorHistory:
    def __init__(self, store):
        self.store = store

    def read(self, offset=0, limit=20, issues_only=False):
        if offset < 0 or not 1 <= limit <= 100:
            raise ValueError("历史分页范围无效")
        condition = "WHERE status IN ('partial','failed','interrupted')" if issues_only else ""
        with self.store.connection() as db:
            db.execute("BEGIN")
            total = db.execute(f"SELECT COUNT(*) FROM runs {condition}").fetchone()[0]
            rows = db.execute(
                f"SELECT runs.*,run_metadata.trigger FROM runs LEFT JOIN run_metadata ON runs.id=job_id "
                f"{condition} ORDER BY created DESC,id DESC LIMIT ? OFFSET ?", (limit, offset),
            ).fetchall()
            failures = {}
            if rows:
                placeholders = ",".join("?" for _ in rows)
                details = db.execute(
                    f"SELECT * FROM run_failures WHERE job_id IN ({placeholders}) ORDER BY job_id,kind,identity",
                    [row["id"] for row in rows],
                ).fetchall()
                for detail in details:
                    failures.setdefault(detail["job_id"], []).append({
                        "kind": detail["kind"], "identity": detail["identity"],
                        "name": detail["name"], "errorCode": detail["error"],
                    })
            items = [{**self.store._public(row), "trigger": row["trigger"] or "unknown",
                "failureDetailsAvailable": row["trigger"] is not None,
                "failures": failures.get(row["id"], [])} for row in rows]
            return {"version": "rule-source-monitor-v1", "items": items, "offset": offset,
                "limit": limit, "total": total}
