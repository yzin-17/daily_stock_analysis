"""本地账号权限记录，按私有 HMAC 绑定凭证与来源端点。"""

from copy import deepcopy
import hmac
import json
from secrets import token_bytes

from src.services.thesis_ledger_account_access_contract import AccountAccessResult
from src.services.thesis_ledger_control import ControlContractError

TABLE = "thesis_ledger_account_access"


def configured_settings(store, provider_id):
    with store._connect() as connection:
        row = connection.execute("SELECT settings_json FROM thesis_ledger_provider_config WHERE provider_id = ?",
                                 (provider_id,)).fetchone()
    return json.loads(row[0]) if row else {}


def configured_level(store, provider_id):
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    source = CAPABILITY_SOURCES.source(provider_id)
    settings = configured_settings(store, provider_id)
    return source.account_access_level.parse(settings.get("accountLevel")) if source and source.account_access_level else None


def _identity(snapshot, level, key):
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    source = CAPABILITY_SOURCES.source(snapshot.provider_id)
    revision = source.account_access_level.revision if source and source.account_access_level else None
    payload = json.dumps([snapshot.provider_id, snapshot.source, snapshot.method, dict(snapshot.values),
                          snapshot.credential_version, level, revision], sort_keys=True).encode()
    return hmac.digest(key, payload, "sha256")


def load_record(store, snapshot, level):
    with store._connect() as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone():
            return None
        key = connection.execute(f"SELECT identity FROM {TABLE} WHERE provider_id='@identity-key'").fetchone()
        row = connection.execute(f"SELECT identity, report_json FROM {TABLE} WHERE provider_id=?",
                                 (snapshot.provider_id,)).fetchone()
    if not key or not row or not hmac.compare_digest(bytes(row[0]), _identity(snapshot, level, bytes(key[0]))):
        return None
    try:
        report = json.loads(row[1])
        if not isinstance(report, dict) or report.get("accountLevel") != level:
            return None
        if not isinstance(report.get("routes"), list) or not 1 <= len(report["routes"]) <= 64:
            return None
        if report.get("credentialContext") != "current" or report.get("persisted") is not True:
            return None
        for route in report["routes"]:
            if not isinstance(route, dict) or any(not isinstance(route.get(k), dict) for k in ("key", "target", "access")):
                return None
            value = {k: v for k, v in route["access"].items() if k in AccountAccessResult.model_fields}
            AccountAccessResult.model_validate(value)
            if route["access"].get("persisted") is not True or not route["access"].get("checkedAt"):
                return None
            route["access"]["configVersion"] = snapshot.config_version
        report["configVersion"] = snapshot.config_version
        return report
    except (ValueError, TypeError, KeyError):
        return None


def write_record(connection, snapshot, level, report):
    persisted = deepcopy(report)
    persisted.update(credentialContext="current", configVersion=snapshot.config_version, persisted=True)
    if level is not None:
        persisted["accountLevel"] = level
    else:
        persisted.pop("accountLevel", None)
    persisted.pop("validUntil", None)
    for route in persisted["routes"]:
        route["access"].pop("validUntil", None)
        route["access"].update(configVersion=snapshot.config_version, persisted=True)
    connection.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} "
                       "(provider_id TEXT PRIMARY KEY, identity BLOB NOT NULL, report_json TEXT NOT NULL)")
    connection.execute(f"INSERT OR IGNORE INTO {TABLE} VALUES ('@identity-key', ?, '{{}}')", (token_bytes(32),))
    key = bytes(connection.execute(f"SELECT identity FROM {TABLE} WHERE provider_id='@identity-key'").fetchone()[0])
    connection.execute(f"INSERT INTO {TABLE} VALUES (?, ?, ?) ON CONFLICT(provider_id) DO UPDATE SET "
                       "identity=excluded.identity, report_json=excluded.report_json",
                       (snapshot.provider_id, _identity(snapshot, level, key), json.dumps(persisted)))
    return persisted


def save_record(store, snapshot, level, report):
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    source = CAPABILITY_SOURCES.source(snapshot.provider_id)
    with store._schema_lock, store._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current = store.provider_credential_snapshot(snapshot.provider_id)
        if source.account_access_snapshot is not None:
            current = source.account_access_snapshot(current)
        if current != snapshot or configured_level(store, snapshot.provider_id) != level:
            raise ControlContractError("PROVIDER_CREDENTIAL_CHANGED", "账号配置变化，权限记录未采用")
        persisted = write_record(connection, snapshot, level, report)
        connection.commit()
    return persisted
