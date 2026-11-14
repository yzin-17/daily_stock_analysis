"""按资料包指纹保存已核验覆盖投影；原件和监测版本不改写。"""

import json


class RuleCoverageStore:
    def __init__(self, monitor_store):
        self.store = monitor_store
        with self.store.connection(True) as db:
            db.execute(
                'CREATE TABLE IF NOT EXISTS rule_material_coverage_v1 ('
                'identity TEXT NOT NULL,pack_hash TEXT NOT NULL,material TEXT,outcome TEXT NOT NULL,'
                'checked REAL NOT NULL,next_check REAL NOT NULL,evidence TEXT NOT NULL,'
                'PRIMARY KEY(identity,pack_hash))'
            )

    def read(self, identity, digest):
        with self.store.connection() as db:
            row = db.execute(
                'SELECT * FROM rule_material_coverage_v1 WHERE identity=? AND pack_hash=?', (identity, digest)
            ).fetchone()
            return dict(row) if row else None

    def begin_check(self, identity, digest, guard):
        with self.store.connection(True) as db:
            if not guard():
                return False
            now = self.store.clock()
            previous = db.execute(
                'SELECT next_check FROM rule_material_coverage_v1 WHERE identity=? AND pack_hash=?', (identity, digest)
            ).fetchone()
            if previous and previous['next_check'] > now:
                return False
            db.execute(
                'INSERT INTO rule_material_coverage_v1 VALUES(?,?,NULL,?,?,?,?) '
                'ON CONFLICT(identity,pack_hash) DO UPDATE SET outcome=excluded.outcome,'
                'checked=excluded.checked,next_check=excluded.next_check,evidence=excluded.evidence',
                (identity, digest, 'refresh_in_progress', now, now + 300, '{}')
            )
            return True

    def publish(self, identity, digest, outcome, evidence, guard, material=None):
        with self.store.connection(True) as db:
            if not guard():
                return False
            now = self.store.clock()
            previous = db.execute(
                'SELECT * FROM rule_material_coverage_v1 WHERE identity=? AND pack_hash=?', (identity, digest)
            ).fetchone()
            if previous and material is None:
                material = json.loads(previous['material']) if previous['material'] else None
            if previous and material and previous['material']:
                old = json.loads(previous['material'])
                if old['coverage']['end'] > material['coverage']['end']:
                    material = old
            db.execute(
                'INSERT INTO rule_material_coverage_v1 VALUES(?,?,?,?,?,?,?) '
                'ON CONFLICT(identity,pack_hash) DO UPDATE SET material=excluded.material,outcome=excluded.outcome,'
                'checked=excluded.checked,next_check=excluded.next_check,evidence=excluded.evidence',
                (identity, digest, json.dumps(material) if material else None, outcome, now, now + 300,
                 json.dumps(evidence, ensure_ascii=False))
            )
            return True
