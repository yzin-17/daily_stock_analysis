"""当前 Catalog 快照、增量与 ACK 的持久化边界。"""

import hashlib
import json
import re
from typing import Any

from src.services.thesis_ledger_control import (
    ControlContractError, CONSUMER_NAMESPACE, CONTROL_CONTRACT_V3_VERSION,
    _json_load, _utc_now,
)


def require_catalog_request(payload: dict[str, Any], *, ack: bool = False) -> None:
    fields = {"contractVersion", "consumer", "requestId"}
    if ack:
        fields |= {"generation", "checksum"}
    if (set(payload) != fields or payload.get("contractVersion") != 3
            or payload.get("consumer") != CONSUMER_NAMESPACE
            or not isinstance(payload.get("requestId"), str)
            or not payload["requestId"].strip()):
        raise ControlContractError("INVALID_CATALOG_REQUEST", "Catalog 请求不符合当前合同", status_code=422)
    if ack and (type(payload["generation"]) is not int or payload["generation"] < 1
                or not isinstance(payload["checksum"], str)
                or not re.fullmatch(r"[a-f0-9]{64}", payload["checksum"])):
        raise ControlContractError("INVALID_CATALOG_REQUEST", "Catalog ACK 身份无效", status_code=422)


def current_catalog_row(row) -> dict[str, Any]:
    items = _json_load(row["items_json"], None)
    generation = int(row["generation"])
    if not isinstance(items, list) or generation < 1 or row["cursor"] != f"generation:{generation}" or not row["complete"]:
        raise ControlContractError("CATALOG_INVALID_STATE", "Catalog 持久化身份无效", status_code=409)
    keys = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"canonicalCode", "market", "instrumentType", "displayName"} or any(not isinstance(v, str) or not v for v in item.values()):
            raise ControlContractError("CATALOG_INVALID_STATE", "Catalog 条目不符合当前合同", status_code=409)
        keys.append((item["canonicalCode"], item["market"], item["instrumentType"]))
    encoded = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(set(keys)) != len(keys) or keys != sorted(keys) or hashlib.sha256(encoded.encode("utf-8")).hexdigest() != row["checksum"]:
        raise ControlContractError("CATALOG_INVALID_STATE", "Catalog checksum 或条目身份无效", status_code=409)
    return {"contractVersion": CONTROL_CONTRACT_V3_VERSION, "generation": generation,
            "checksum": row["checksum"], "cursor": row["cursor"], "complete": True, "items": items}


def catalog_snapshot(self, cursor: str | None = None) -> dict[str, Any]:
    with self._connect() as connection:
        row = self._ensure_catalog(connection)
        if cursor is not None and cursor != row["cursor"]:
            raise ControlContractError(
                "CATALOG_CURSOR_EXPIRED",
                "Catalog cursor 已过期，需要完整 snapshot",
                status_code=409,
            )
        return current_catalog_row(row)

def catalog_delta(self, cursor: str) -> dict[str, Any]:
    with self._connect() as connection:
        latest = self._ensure_catalog(connection)
        current_catalog_row(latest)
        if cursor == latest["cursor"]:
            return {
                "contractVersion": CONTROL_CONTRACT_V3_VERSION,
                "generation": int(latest["generation"]),
                "checksum": latest["checksum"],
                "cursor": latest["cursor"],
                "complete": True,
                "fromCursor": cursor,
                "items": [],
                "deleted": [],
            }
        previous = connection.execute(
            "SELECT * FROM thesis_ledger_catalog_generation WHERE cursor = ?",
            (cursor,),
        ).fetchone()
        if previous is None:
            raise ControlContractError(
                "CATALOG_CURSOR_EXPIRED",
                "Catalog cursor 已过期，需要完整 snapshot",
                status_code=409,
            )
        current_catalog_row(previous)
        previous_items = {
            (item["canonicalCode"], item["market"], item["instrumentType"]): item
            for item in _json_load(previous["items_json"], [])
        }
        latest_items = {
            (item["canonicalCode"], item["market"], item["instrumentType"]): item
            for item in _json_load(latest["items_json"], [])
        }
        changed = [
            latest_items[key]
            for key in sorted(latest_items)
            if previous_items.get(key) != latest_items[key]
        ]
        deleted = [
            {
                "canonicalCode": key[0],
                "market": key[1],
                "instrumentType": key[2],
            }
            for key in sorted(set(previous_items) - set(latest_items))
        ]
        return {
            "contractVersion": CONTROL_CONTRACT_V3_VERSION,
            "generation": int(latest["generation"]),
            "checksum": latest["checksum"],
            "cursor": latest["cursor"],
            "complete": True,
            "fromCursor": cursor,
            "items": changed,
            "deleted": deleted,
        }

def catalog_ack(self, payload: dict[str, Any]) -> dict[str, Any]:
    require_catalog_request(payload, ack=True)
    with self._schema_lock, self._connect() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            snapshot = current_catalog_row(self._ensure_catalog(connection))
            if payload["generation"] != snapshot["generation"] or payload["checksum"] != snapshot["checksum"]:
                raise ControlContractError("CATALOG_CHECKSUM_MISMATCH", "Catalog generation 或 checksum 不匹配", status_code=409)
            connection.execute(
                """INSERT INTO thesis_ledger_catalog_ack
                (consumer, generation, checksum, cursor, acknowledged_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(consumer) DO UPDATE SET generation=excluded.generation,
                checksum=excluded.checksum, cursor=excluded.cursor, acknowledged_at=excluded.acknowledged_at""",
                (CONSUMER_NAMESPACE, snapshot["generation"], snapshot["checksum"], snapshot["cursor"], _utc_now()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
    return {"contractVersion": 3, "consumer": CONSUMER_NAMESPACE, "requestId": payload["requestId"],
            "acknowledged": True, "generation": snapshot["generation"], "checksum": snapshot["checksum"], "cursor": snapshot["cursor"]}
