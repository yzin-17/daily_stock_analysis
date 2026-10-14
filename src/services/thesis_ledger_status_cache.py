"""按请求隔离的股票结构化状态缓存，复用 DSA 数据目录。"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping


class HistoricalStatusCache:
    def __init__(self, directory: Path | None = None, *, clock=lambda: datetime.now(timezone.utc)):
        if directory is None:
            from src.services.thesis_ledger_control import _database_path
            directory = Path(_database_path()).expanduser().parent / "historical-status-cache"
        self.directory = directory
        self.clock = clock

    def _path(self, identity: Mapping[str, Any]) -> Path:
        digest = sha256(json.dumps(dict(identity), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return self.directory / f"{digest}.json"

    def get(self, identity: Mapping[str, Any]) -> dict[str, Any] | None:
        try:
            entry = json.loads(self._path(identity).read_text())
            payload = entry["value"]
            expires = datetime.fromisoformat(entry["expiresAt"])
            digest = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if entry["identity"] != dict(identity) or entry["sha256"] != digest or expires <= self.clock():
                return None
            return payload
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def put(self, identity: Mapping[str, Any], value: dict[str, Any]) -> None:
        # 空响应、完全未知和异常不能抹掉此前实际取得的有效事实。
        if not any(day["state"] != "unknown" or day["isST"] in {"0", "1"} for day in value["days"]):
            return
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        entry = {"identity": dict(identity), "value": value,
                 "expiresAt": (self.clock() + timedelta(hours=24)).isoformat(),
                 "sha256": sha256(payload.encode()).hexdigest()}
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(identity)
        fd, temp = tempfile.mkstemp(prefix=path.stem + ".", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(entry, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
