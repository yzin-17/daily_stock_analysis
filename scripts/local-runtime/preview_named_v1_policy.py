"""预览或生成 DSA Control 具名策略的隔离 SQLite 副本。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

TABLES = ("thesis_ledger_policy_state", "thesis_ledger_policy_history")


def converted_effective(row: sqlite3.Row) -> dict:
    effective = json.loads(row["effective_json"])
    version = effective.get("contractVersion")
    if (
        type(version) is not int
        or version not in (1, 3)
        or effective.get("consumer") != row["consumer"]
        or effective.get("sourceDesiredRevision") != row["revision"]
        or type(effective.get("enabled")) is not bool
        or effective["enabled"] != bool(row["enabled"])
        or not isinstance(effective.get("routes"), list)
    ):
        raise ValueError("原生效投影与保存的策略身份不一致")
    return {**effective, "contractVersion": 1}


def preview(path: Path) -> dict:
    def digest(value: dict) -> str:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        tables = {}
        for table in TABLES:
            rows = connection.execute(
                f"SELECT consumer, revision, enabled, effective_json FROM {table} ORDER BY consumer, revision"
            ).fetchall()
            changes = []
            blocked = []
            for row in rows:
                try:
                    candidate = converted_effective(row)
                    if json.loads(row["effective_json"])["contractVersion"] == 3:
                        changes.append({
                            "consumer": row["consumer"],
                            "revision": row["revision"],
                            "sourceHash": hashlib.sha256(row["effective_json"].encode("utf-8")).hexdigest(),
                            "projectionHash": digest(candidate),
                        })
                except (ValueError, TypeError, AttributeError):
                    blocked.append({"consumer": row["consumer"], "revision": row["revision"]})
            tables[table] = {"rows": len(rows), "convert": changes, "blocked": blocked}
        connection.rollback()
        return {"contract": "dsa-control-policy-migration-preview-v1", "tables": tables}


def apply_status_policy(connection: sqlite3.Connection, plan: dict) -> dict:
    if plan["format"] != "named-v1-status-policy-stage-v1":
        raise ValueError("未知市场状态换绑清单")
    previous, next_policy, projection = plan["previous"], plan["next"], plan["effective"]
    consumer, revision = previous["consumer"], previous["revision"]
    current = connection.execute(
        "SELECT * FROM thesis_ledger_policy_state WHERE consumer=?", (consumer,)
    ).fetchone()
    if (current is None or current["status"] != "applied" or current["revision"] != revision
            or bool(current["enabled"]) != previous["enabled"]
            or json.loads(current["routes_json"]) != previous["routes"]):
        raise ValueError("DSA 活动策略与原 Server 清单不一致")
    if (next_policy["consumer"] != consumer or next_policy["revision"] != revision + 1
            or next_policy["enabled"] != previous["enabled"] or next_policy["contractVersion"] != 1
            or projection["sourceDesiredRevision"] != next_policy["revision"]
            or projection["requestId"] != next_policy["requestId"]):
        raise ValueError("市场状态换绑改变了策略身份或启停")
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    effective = {**projection, "appliedAt": now}
    values = (consumer, next_policy["revision"], int(next_policy["enabled"]),
              json.dumps(next_policy["routes"], ensure_ascii=False, separators=(",", ":")),
              json.dumps(effective, ensure_ascii=False, separators=(",", ":")), next_policy["requestId"], now)
    connection.execute("""
        INSERT INTO thesis_ledger_policy_history
        (consumer, revision, enabled, routes_json, status, effective_json,
         last_error_json, request_id, created_at)
        VALUES (?, ?, ?, ?, 'applied', ?, NULL, ?, ?)
    """, values)
    updated = connection.execute("""
        UPDATE thesis_ledger_policy_state SET revision=?, enabled=?, routes_json=?, status='applied',
          effective_json=?, last_error_json=NULL, request_id=?, updated_at=?
        WHERE consumer=? AND revision=?
    """, (*values[1:], consumer, revision))
    if updated.rowcount != 1:
        raise ValueError("DSA 活动策略已变化")
    return effective


def stage_copy(source: Path, destination: Path, status_plan: dict | None = None) -> dict:
    """在独立副本内核验并转换；活动库只通过 SQLite 只读连接访问。"""
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        # 状态换绑只接收部署脚本导出的独立 SQLite 备份。
        uri = source.resolve().as_uri() + ("?mode=ro&immutable=1" if status_plan else "?mode=ro")
        if status_plan and any(Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm")):
            raise ValueError("市场状态换绑需要包含全部已提交页面的独立 SQLite 备份")
        with closing(sqlite3.connect(uri, uri=True)) as origin:
            origin.execute("PRAGMA query_only=ON")
            with closing(sqlite3.connect(destination)) as copy:
                origin.backup(copy)
                copy.row_factory = sqlite3.Row
                if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite 源副本完整性校验失败")
                copy.execute("BEGIN IMMEDIATE")
                counts = {}
                for table in TABLES:
                    rows = copy.execute(
                        f"SELECT consumer, revision, enabled, effective_json FROM {table}"
                    ).fetchall()
                    changed = 0
                    for row in rows:
                        candidate = converted_effective(row)
                        if json.loads(row["effective_json"]) == candidate:
                            continue
                        update = copy.execute(
                            f"UPDATE {table} SET effective_json=? WHERE consumer=? AND revision=?",
                            (json.dumps(candidate, ensure_ascii=False, separators=(",", ":")),
                             row["consumer"], row["revision"]),
                        )
                        if update.rowcount != 1:
                            raise ValueError("SQLite 策略修订身份不唯一")
                        changed += 1
                    counts[table] = {"rows": len(rows), "converted": changed}
                effective = apply_status_policy(copy, status_plan) if status_plan else None
                if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("SQLite 转换副本完整性校验失败")
                copy.commit()
        summary = {"contract": "dsa-control-policy-migration-stage-v1", "tables": counts}
        return {**summary, "effective": effective} if effective is not None else summary
    except BaseException:
        destination.unlink()
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("--staging-copy", type=Path)
    parser.add_argument("--status-plan", type=Path)
    args = parser.parse_args()
    if args.status_plan and not args.staging_copy:
        parser.error("市场状态换绑必须生成隔离副本")
    plan = json.loads(args.status_plan.read_text()) if args.status_plan else None
    result = stage_copy(args.database, args.staging_copy, plan) if args.staging_copy else preview(args.database)
    print(json.dumps(result, ensure_ascii=False))
