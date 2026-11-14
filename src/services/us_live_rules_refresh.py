"""当前美国规则按需核查；复用监测队列，每个日期及版本仅一批，不阻塞报价。"""

from hashlib import sha256
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from uuid import uuid5, NAMESPACE_URL

IDENTITY = 'official-us-rules/us-live-cash-equity'
DOCUMENTS = (
    ('价格规则延期', 'https://www.sec.gov/files/rules/exorders/2026/34-105656.pdf',
     'f11d3b90cabe2d2432eef3b2f56199ae8c411d6e0c6a12ffd08d1fd9053fc7e9'),
    ('证券交易费率', 'https://www.sec.gov/rules-regulations/fee-rate-advisories/2026-2',
     '7843dd56c22390c5ca54457232fd3b572cffda370a1127b7b16fd3046fad6eab'),
    ('现行证券交易费目录', 'https://www.sec.gov/rules-regulations/fee-rate-advisories',
     '28831d57bf6a4373f947c79cb9886ee69d22af1b67b582b846c5bf01b7566ac7'),
    ('TAF 暂停收费生效通知', 'https://www.sec.gov/files/rules/sro/finra/2026/34-106409.pdf',
     'b45080f51ebf9208784588bed02da224ae76f64675deec6cc1ad005ad9393aae'),
    ('TAF 暂停收费正文', 'https://www.sec.gov/files/rules/sro/finra/2026/34-106409-ex5.pdf',
     '6dc60f294ecd26f46c06784bcdab9fe09c709753a628b97d13970d0291b0be59'),
    ('FINRA 当前修订目录',
     'https://www.sec.gov/rules-regulations/self-regulatory-organization-rulemaking/finra?order=field_publish_date&page=0&sort=desc',
     '65fdf1d3931862ddf43c34164586b68c02530dec35df43bb57b06c0e9f8b1abe'),
)


def registered_source():
    from src.services.rule_monitor_registry import Source
    from src.services.rule_monitor_snapshot import text_snapshot
    from src.services.us_live_execution_rules import BASE_RULES, VERIFIED_THROUGH
    pack = {**BASE_RULES, 'datasetId': BASE_RULES['ruleSetId'],
            'verifiedThrough': VERIFIED_THROUGH, 'refreshBaseline': DOCUMENTS}
    raw = json.dumps(pack, ensure_ascii=False, sort_keys=True)
    documents = tuple({'id': str(i), 'title': title, 'url': url} for i, (title, url, _) in enumerate(DOCUMENTS))
    return Source(IDENTITY, 'US', '美国当前现金证券执行规则', pack['datasetId'],
                  sha256(raw.encode()).hexdigest(), documents, text_snapshot('json', raw))


def checked_through(day, context, *, monitor=None):
    """返回完整、未变资料批次的核查时刻；失败或新增资料不会被下一次相同抓取洗白。"""
    if not context or not context.database_path or not context.publish_guard or not context.publish_guard():
        return None
    from src.services.rule_monitor_runtime import create_rule_monitor_runtime
    monitor = monitor or create_rule_monitor_runtime(context.database_path)
    today = datetime.fromtimestamp(monitor.store.clock(), timezone.utc).astimezone(ZoneInfo('America/New_York')).date().isoformat()
    if day != today:
        return None
    source = registered_source()
    request_id = str(uuid5(NAMESPACE_URL, IDENTITY + ':' + day + ':' + source.digest))
    job = monitor.store.job(request_id)
    observations = monitor.observations.status()
    pack = next((row for row in observations if row['kind'] == 'pack' and row['identity'] == IDENTITY), None)
    # 显式手动复核成功后可以恢复同日证据，原失败批次保留且不自动补投。
    with monitor.store.connection() as db:
        completed = db.execute("SELECT id FROM runs WHERE status='succeeded' AND sources=? ORDER BY finished DESC LIMIT 1",
                               (json.dumps([IDENTITY]),)).fetchone()
    if completed and pack and pack['contentHash'] == source.digest:
        candidate = monitor.store.job(completed['id'])
        finished_day = datetime.fromisoformat(candidate['finishedAt']).astimezone(ZoneInfo('America/New_York')).date().isoformat()
        if finished_day == day:
            job = candidate
    if job is None:
        try:
            monitor.enqueue(request_id, [IDENTITY])
        except ValueError:
            pass  # 其他资料批次持锁时保留不可用，后续请求仍按同一身份查询。
        return None
    if job['status'] != 'succeeded' or job['counts']['unavailable'] or job['counts']['documents'] != len(DOCUMENTS):
        return None
    if (not pack or pack['contentHash'] != source.digest
            or not job['startedAt'] <= pack['lastCheckedAt'] <= job['finishedAt']):
        return None
    rows = {row['identity']: row for row in observations if row['kind'] == 'document'}
    for _, url, baseline in DOCUMENTS:
        row = rows.get(url)
        if (not row or row['errorCode'] or row['contentHash'] != baseline
                or not job['startedAt'] <= row['lastCheckedAt'] <= job['finishedAt']):
            return None
        with monitor.store.connection() as db:
            snapshot = db.execute('SELECT body,reason FROM version_snapshots WHERE kind=? AND identity=? AND digest=?',
                                  ('document', url, baseline)).fetchone()
            if not snapshot or not snapshot['body'] or snapshot['reason']:
                return None
    if not context.publish_guard() or monitor.registry()[IDENTITY].digest != source.digest:
        return None
    return job['finishedAt']
