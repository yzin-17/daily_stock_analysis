"""DSA 留存对账：基线只读解析、目标逐对象核对及真实存储读取。"""

import base64
from collections import Counter
from contextlib import closing
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import socket
import sqlite3
import sys
import tarfile
from urllib.parse import quote


def digest(value):
    return hashlib.sha256(value).hexdigest()


def encoded(value):
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    return [type(value).__name__, value]


def row_digest(row):
    return digest(json.dumps([encoded(v) for v in row], ensure_ascii=False,
                             separators=(",", ":")).encode())


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def readonly_database(path):
    source = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True)
    source.execute("PRAGMA query_only=ON")
    memory = sqlite3.connect(":memory:")
    try:
        source.backup(memory)
    finally:
        source.close()
    memory.row_factory = sqlite3.Row
    memory.execute("PRAGMA query_only=ON")
    assert memory.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "SQLite 完整性失败"
    return memory


def database_inventory(connection, baseline=None):
    result = {}
    names = [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    if baseline:
        assert set(baseline) <= set(names), "原表缺失"
    for name in names:
        columns = [dict(r) for r in connection.execute("PRAGMA table_info(" + quoted(name) + ")")]
        selected = [c["name"] for c in columns]
        if baseline and name in baseline:
            prior = baseline[name]["columns"]
            present = {c["name"]: c for c in columns}
            assert all(c["name"] in present and all(present[c["name"]][k] == c[k]
                       for k in ("type", "notnull", "dflt_value", "pk")) for c in prior), "原列定义变更"
            selected = [c["name"] for c in prior]
        rows = connection.execute("SELECT " + ",".join(map(quoted, selected)) + " FROM " + quoted(name))
        result[name] = {"columns": columns, "rows": sorted(row_digest(row) for row in rows)}
    return result


def archive_inventory(path):
    files = {}
    with tarfile.open(path) as archive:
        for member in archive:
            relative = PurePosixPath(member.name)
            assert not relative.is_absolute() and ".." not in relative.parts, "备份路径越界"
            if member.isdir():
                continue
            assert member.isfile(), "备份包含非普通文件"
            name = str(relative)
            assert name not in files, "备份对象重复"
            with archive.extractfile(member) as stream:
                content = stream.read()
            files[name] = {"bytes": len(content), "sha256": digest(content)}
    return files


def file_inventory(root, database_path=None):
    files = {}
    omitted = set()
    if database_path:
        omitted = {Path(str(database_path) + suffix) for suffix in ("", "-wal", "-shm")}
    for path in sorted(root.rglob("*")):
        assert not path.is_symlink(), "目标目录包含符号链接"
        if path.is_dir() or path in omitted:
            continue
        assert path.is_file(), "目标包含非普通文件"
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        assert (before.st_ino, before.st_size, before.st_mtime_ns) == (
            after.st_ino, after.st_size, after.st_mtime_ns), "读取期间文件变化"
        files[str(path.relative_to(root))] = {"bytes": len(content), "sha256": digest(content)}
    return files


def compare_objects(baseline, current):
    missing = sorted(set(baseline) - set(current))
    changed = sorted(name for name in baseline.keys() & current.keys() if baseline[name] != current[name])
    assert not missing, "原文件缺失：" + ",".join(digest(x.encode()) for x in missing)
    assert not changed, "原文件变化：" + ",".join(digest(x.encode()) for x in changed)
    return [{"objectId": digest(name.encode()), **current[name], "added": name not in baseline}
            for name in sorted(current)]


def compare_tables(baseline, current):
    assert set(baseline) <= set(current), "原表缺失"
    result = []
    for name, old in baseline.items():
        previous, now = Counter(old["rows"]), Counter(current[name]["rows"])
        assert not (previous - now), "原记录缺失或变化：" + name
        result.append({"table": name, "originalRows": sum(previous.values()),
                       "currentRows": sum(now.values()), "addedRows": sum((now - previous).values()),
                       "originalRowsSha256": digest(json.dumps(old["rows"]).encode())})
    for name in sorted(set(current) - set(baseline)):
        result.append({"table": name, "originalRows": 0, "currentRows": len(current[name]["rows"]),
                       "addedRows": len(current[name]["rows"]), "addedTable": True})
    return result


def read_credentials(connection):
    from src.services.thesis_ledger_control import _decrypt_secret
    from src.services.provider_credentials import decode_credential_plaintext, validate_stored_credential
    from src.services.provider_oauth_contract import validate_oauth_token

    results = []
    for row in connection.execute("SELECT * FROM thesis_ledger_provider_config ORDER BY provider_id"):
        if not row["credential_ciphertext"]:
            continue
        try:
            credential = decode_credential_plaintext(_decrypt_secret(
                row["secret_key_version"], row["credential_ciphertext"]))
            validate_stored_credential(row["provider_id"], credential)
            if credential.method == "oauth":
                validate_oauth_token(credential.values.get("clientId", ""), credential.values.get("tokenJson", ""))
        except Exception:
            raise AssertionError("原凭据无法解密或解析") from None
        results.append({"providerId": row["provider_id"], "method": credential.method,
                        "credentialVersion": row["credential_version"],
                        "ciphertextSha256": digest(row["credential_ciphertext"].encode()), "readable": True})
    return results


def read_fx(connection):
    from src.data.fx_rate_repository import FxRateRepository
    from src.data_config import DataServiceConfig
    from src.services.fx_rate_service import cached_fx_rate, fx_row

    repo = FxRateRepository(config=DataServiceConfig.from_env(os.environ))
    # 注入同一目标的只读快照，复用真实查询函数，避免初始化或写入活动库。
    repo._connection = connection
    result = {"persistedRows": 0, "directReads": 0, "inverseReads": 0, "unknownFetchedAt": 0}
    for row in connection.execute("SELECT * FROM portfolio_fx_rates"):
        as_of = date.fromisoformat(row["rate_date"])
        args = {"repo": repo, "from_currency": row["from_currency"],
                "to_currency": row["to_currency"], "as_of": as_of}
        direct = cached_fx_rate(**args)
        inverse = cached_fx_rate(**{**args, "from_currency": row["to_currency"],
                                   "to_currency": row["from_currency"]})
        assert direct and inverse and math.isclose(direct["rate"], float(row["rate"])), "原 FX 无法读取"
        reverse_exists = connection.execute(
            "SELECT 1 FROM portfolio_fx_rates WHERE from_currency=? AND to_currency=? AND rate_date<=?",
            (row["to_currency"], row["from_currency"], as_of.isoformat())).fetchone()
        if not reverse_exists:
            assert math.isclose(inverse["rate"], 1 / float(row["rate"])), "原 FX 反向读取失败"
        value = fx_row(from_currency=row["from_currency"], to_currency=row["to_currency"],
                       rate=direct["rate"], rate_date=direct["rate_date"], provider=direct["provider"],
                       fetched_at=direct["fetched_at"], stale=direct["stale"], as_of=as_of)
        assert ("fetchedAt" in value) == bool(direct["fetched_at"]), "FX 未知时间被补造"
        result["persistedRows"] += 1
        result["directReads"] += 1
        result["inverseReads"] += 1
        result["unknownFetchedAt"] += int("fetchedAt" not in value)
    return result


def read_artifacts(root):
    from src.services.thesis_ledger_announcements import AnnouncementRepository
    from src.services.thesis_ledger_announcement_jobs import AnnouncementJobs

    repo = AnnouncementRepository(root / "thesis-ledger-announcements")
    records = repo.records()
    originals = []
    for row in records:
        if row.get("downloadState") == "downloaded":
            raw = repo.original(row)
            originals.append({"recordIdSha256": digest(row["id"].encode()),
                              "bytes": len(raw), "sha256": digest(raw)})
    jobs = AnnouncementJobs(repo.root)
    states = Counter()
    for path in sorted(jobs.root.glob("*.json")):
        if path.stem in ("usage", "schedule"):
            continue
        row = jobs.read(path.stem)
        assert row["id"] == path.stem and row["state"] in ("queued", "running", "succeeded", "failed")
        assert 0 <= row["attempt"] <= jobs.MAX_ATTEMPTS, "历史任务重试次数越界"
        states[row["state"]] += 1
    queries = list((repo.root / ".queries").glob("*.json"))
    for path in queries:
        value = json.loads(path.read_text())
        assert isinstance(value["items"], list) and isinstance(value["finishedAt"], (int, float))
    return {"announcementRecords": len(records), "originals": originals,
            "announcementJobs": dict(states), "queryCaches": len(queries)}


def blocked_network(*args, **kwargs):
    raise AssertionError("留存验收禁止出站请求")


def verify_retained(baseline):
    assert baseline["format"] == "dsa-retention-baseline-v1"
    path = Path(os.environ["DATABASE_PATH"]).resolve()
    assert path == Path("/app/data/stock_analysis.db"), "目标数据路径不符"
    with closing(readonly_database(path)) as connection:
        tables = compare_tables(baseline["tables"], database_inventory(connection, baseline["tables"]))
    objects = compare_objects(baseline["files"], file_inventory(path.parent, path))
    assert not sum(r["addedRows"] for r in tables) and not any(o["added"] for o in objects), "目标出现未登记增量"
    return {"passed": True, "tables": tables, "objects": objects, "readOnly": True}


def audit_live(baseline):
    assert baseline["format"] == "dsa-retention-baseline-v1"
    socket.socket.connect = blocked_network
    socket.socket.connect_ex = blocked_network
    socket.create_connection = blocked_network
    path = Path(os.environ["DATABASE_PATH"]).resolve()
    assert path.parent == Path("/app/data") and path.name == "stock_analysis.db", "目标数据路径不符"
    files = file_inventory(path.parent, path)
    objects = compare_objects(baseline["files"], files)
    with closing(readonly_database(path)) as connection:
        current = database_inventory(connection, baseline["tables"])
        tables = compare_tables(baseline["tables"], current)
        credentials = read_credentials(connection)
        fx = read_fx(connection)
        catalog = Counter(row[0] for row in connection.execute("SELECT status FROM thesis_ledger_catalog_job"))
        for table, columns in {"thesis_ledger_catalog_generation": ["items_json"],
                               "thesis_ledger_policy_history": ["routes_json", "effective_json", "last_error_json"],
                               "thesis_ledger_provider_config": ["settings_json"]}.items():
            for row in connection.execute("SELECT " + ",".join(columns) + " FROM " + table):
                for value in row:
                    if value:
                        json.loads(value)
    artifacts = read_artifacts(path.parent)
    reports = file_inventory(Path("/app/reports"))
    oauth_home = Path(os.environ.get("HOME", "/home/dsa")) / ".longbridge"
    home_files = file_inventory(oauth_home)
    with closing(readonly_database(path)) as connection:
        assert database_inventory(connection, baseline["tables"]) == current, "验收期间数据库逻辑状态变化"
    assert file_inventory(path.parent, path) == files, "验收期间数据文件变化"
    additions = sum(r["addedRows"] for r in tables) + sum(o["added"] for o in objects)
    assert not additions, "有新增对象待单独登记，不能自动认定留存通过"
    assert not reports and not home_files, "原零基数目录出现对象，须单独核对"
    return {"format": "dsa-retention-target-v1", "readOnly": True, "networkRequests": 0,
            "tables": tables, "objects": objects, "credentials": credentials, "fx": fx,
            "catalogJobs": dict(catalog), "artifacts": artifacts,
            "reports": {"exists": Path("/app/reports").is_dir(), "files": len(reports)},
            "oauthHome": {"exists": oauth_home.is_dir(), "files": len(home_files)},
            "addedObjects": additions, "passed": True}


def main(args):
    if args[0] == "baseline":
        with closing(readonly_database(Path(args[1]))) as connection:
            value = {"format": "dsa-retention-baseline-v1", "tables": database_inventory(connection),
                     "files": archive_inventory(args[2])}
    elif args == ["live"]:
        value = audit_live(json.load(sys.stdin))
    elif args == ["verify"]:
        value = verify_retained(json.load(sys.stdin))
    else:
        raise ValueError("未知留存验收模式")
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as error:
        # 不输出可能携带私密字段的第三方异常内容或 traceback。
        print(json.dumps({"passed": False, "errorType": type(error).__name__,
                          "reason": str(error) if isinstance(error, AssertionError) else "留存读取失败"}))
        sys.exit(1)
