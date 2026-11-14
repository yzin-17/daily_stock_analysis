"""按资料身份读取已保存正文并生成有界差异，不访问外部来源。"""

from difflib import SequenceMatcher

from src.services.rule_monitor_runtime import VERSION


def compare_snapshots(base, target):
    if base["text"] is None or target["text"] is None:
        return {"status": "unavailable", "reason": "snapshot_unavailable", "added": 0, "removed": 0, "lines": []}
    old, new = base["text"].splitlines(), target["text"].splitlines()
    if len(base["text"]) + len(target["text"]) > 200_000 or len(old) + len(new) > 4000:
        return {"status": "unavailable", "reason": "diff_too_large", "added": 0, "removed": 0, "lines": []}
    lines = []
    added = removed = 0
    matcher = SequenceMatcher(None, old, new, autojunk=False)
    for group in matcher.get_grouped_opcodes(3):
        if lines:
            lines.append({"type": "separator", "text": "…"})
        for tag, i, j, a, b in group:
            if tag == "equal":
                lines.extend({"type": "context", "text": text} for text in old[i:j])
            if tag in {"delete", "replace"}:
                removed += j - i
                lines.extend({"type": "removed", "text": text} for text in old[i:j])
            if tag in {"insert", "replace"}:
                added += b - a
                lines.extend({"type": "added", "text": text} for text in new[a:b])
        if len(lines) > 2000:
            return {"status": "unavailable", "reason": "diff_too_large", "added": 0, "removed": 0, "lines": []}
    return {"status": "available", "reason": None, "added": added, "removed": removed, "lines": lines}


class RuleMonitorVersions:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _read(db, kind, identity, digest):
        row = db.execute("SELECT v.*,s.format,s.body,s.reason,s.captured FROM versions v "
            "LEFT JOIN version_snapshots s ON v.kind=s.kind AND v.identity=s.identity AND v.digest=s.digest "
            "WHERE v.kind=? AND v.identity=? AND v.digest=?", (kind, identity, digest)).fetchone()
        if row is None:
            raise KeyError("rule_monitor_version_not_found")
        return {"status": "available" if row["body"] is not None else "unavailable",
            "format": row["format"], "text": row["body"], "capturedAt": row["captured"],
            "reason": row["reason"] if row["captured"] is not None else "legacy_missing"}

    def read(self, kind, identity, digest, base_hash=None):
        with self.store.connection() as db:
            db.execute("BEGIN")
            snapshot = self._read(db, kind, identity, digest)
            comparison = None
            if base_hash is not None:
                base = self._read(db, kind, identity, base_hash)
                comparison = {"baseHash": base_hash, **compare_snapshots(base, snapshot)}
        return {"version": VERSION, "kind": kind, "identity": identity, "contentHash": digest,
            "snapshot": snapshot, "comparison": comparison}
