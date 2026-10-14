"""目录只读复用已有事件准入；不读取原页、签发或保存凭据。"""

from datetime import datetime, timezone

from src.services.thesis_ledger_event_admission_v3 import event_admission_snapshot


def catalog_event_admissions(entries, current_admission):
    if current_admission is None:
        return []
    result = []
    for entry in entries:
        key = entry['key']
        if (entry['state'] != 'ready' or key['kind'] != 'data'
                or key['capability'] not in {'CASH_DISTRIBUTION', 'SPLIT_EVENT'}):
            continue
        try:
            record = current_admission(key, entry['target'])
            snapshot = event_admission_snapshot(record, observed_at=datetime.now(timezone.utc))
            if snapshot['routeKey'] == key and snapshot['target'] == entry['target']:
                result.append(snapshot)
        except Exception:
            # 无法核实的快照不能填入显式视图；消费端须拒绝缺失。
            continue
    return result
