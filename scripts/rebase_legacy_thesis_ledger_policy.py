"""Explicitly retire one legacy ThesisLedger policy row in a development SQLite DB.

The historical rows, route admissions, provider settings and credentials remain intact.
The next V3 policy must be applied through the authenticated Control endpoint.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path


CONSUMER = "thesis-ledger"


def rebase(database: Path, backup: Path, confirmation: str) -> int:
    database = database.resolve(strict=True)
    expected = f"{database}:{CONSUMER}:"
    if not confirmation.startswith(expected):
        raise ValueError("confirmation does not name the exact database and consumer")
    try:
        revision = int(confirmation[len(expected):])
    except ValueError as error:
        raise ValueError("confirmation must include the current revision") from error
    if revision <= 0:
        raise ValueError("confirmation revision must be positive")
    backup = backup.resolve()
    if backup == database or backup.exists():
        raise ValueError("backup must be a new, separate file")

    with sqlite3.connect(database, timeout=10) as connection:
        row = connection.execute(
            "SELECT revision, routes_json, effective_json FROM thesis_ledger_policy_state "
            "WHERE consumer = ?", (CONSUMER,),
        ).fetchone()
        if row is None or row[0] != revision:
            raise ValueError("stored policy revision differs from confirmation")
        routes = json.loads(row[1])
        effective = json.loads(row[2])
        if (not isinstance(routes, dict) or not isinstance(effective, dict)
                or effective.get("contractVersion") == 3):
            raise ValueError("stored policy is not the expected legacy format")

        backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)
        try:
            with sqlite3.connect(backup) as destination:
                connection.backup(destination)
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT revision, routes_json FROM thesis_ledger_policy_state "
                "WHERE consumer = ?", (CONSUMER,),
            ).fetchone()
            if current != (revision, row[1]):
                raise ValueError("stored policy changed during backup")
            cursor = connection.execute(
                "DELETE FROM thesis_ledger_policy_state WHERE consumer = ? AND revision = ?",
                (CONSUMER, revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("stored policy changed during rebase")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return revision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    revision = rebase(args.database, args.backup, args.confirm)
    print(f"retired legacy current policy revision {revision}; historical rows preserved")


if __name__ == "__main__":
    main()
