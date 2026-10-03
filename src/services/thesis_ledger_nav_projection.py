"""NAV 精确证据的稳定投影；上游原生字节与 DSA 记录分别保存。"""

import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from data_provider.eastmoney_nav_evidence import canonical_json, hash_raw


def project_nav_evidence(request, source, identity, rule_evidence, date_evidence, captured_at):
    calendar = date_evidence.calendar
    rule = rule_evidence.rule
    first = calendar['coverage']['startDate']
    selected = sorted((record for record in source.records if first <= record.valuation_date <= request['end']),
                      key=lambda record: record.valuation_date)
    records, facts, publication = [], [], []
    for native in selected:
        record_id = f"eastmoney-nav-raw-v1:{request['symbol']}:{native.content_hash}"
        record = {'sourceRecordId': record_id, 'symbol': request['symbol'],
                  'valuationDate': native.valuation_date, 'nav': native.unit_nav,
                  'projectionRevision': 'dsa-eastmoney-nav-record-v1',
                  'nativeRecordRaw': native.native_record_raw, 'nativeRecordHash': native.content_hash,
                  'sourcePageIndex': native.page_index}
        raw = canonical_json(record)
        following = [day for day in calendar['disclosureWorkDates'] if day > native.valuation_date]
        disclosed = following[rule['delayWorkdays'] - 1]
        visible = datetime.fromisoformat(disclosed).replace(tzinfo=ZoneInfo('Asia/Shanghai')) + timedelta(days=1)
        records.append(record)
        publication.append({'sourceRecordId': record_id, 'rawRecord': raw})
        facts.append({
            'symbol': request['symbol'], 'valuationDate': native.valuation_date,
            'nav': native.unit_nav, 'status': 'supported', 'freshness': 'delayed', 'quality': 'complete',
            'occurredAt': f'{native.valuation_date}T00:00:00+08:00', 'availableAt': visible.isoformat(),
            'publicationEvidence': {
                'kind': 'research-assumption', 'sourceRecordId': record_id, 'rawRecordHash': hash_raw(raw),
                'evidenceRef': rule['evidenceRef'],
                'ruleHash': rule['contentHash'], 'disclosureCalendarHash': calendar['contentHash'],
                'disclosureDate': disclosed, 'assumedAvailableAt': visible.isoformat(),
            },
        })
    response_raw = canonical_json({
        'schemaVersion': 'nav-source-record-envelope-v1', 'symbol': request['symbol'],
        'fundType': identity.fund_type, 'identity': identity.record, 'records': records,
        'navSource': {'fundCode': source.fund_code, 'endpoint': source.endpoint,
                      'readerRevision': source.reader_revision, 'capturedAt': source.captured_at,
                      'totalCount': source.total_count, 'contentHash': source.content_hash,
                      'pages': [{'pageIndex': page.page_index, 'rawResponse': page.raw_response,
                                 'contentHash': page.content_hash} for page in source.pages]},
        'ruleRaw': rule_evidence.rule_raw,
        'ruleDocument': {'kind': rule_evidence.document_kind, 'encoding': 'base64',
                         'raw': base64.b64encode(rule_evidence.document_raw).decode('ascii'),
                         'contentHash': rule['documentHash'], 'capturedAt': rule_evidence.captured_at,
                         'readerRevision': rule_evidence.reader_revision},
        'calendarRaw': date_evidence.calendar_raw, 'assumptionRaw': date_evidence.assumption_raw,
        'calendarDecisionRaw': date_evidence.decision_raw, 'capturedAt': captured_at,
    })
    if len(response_raw.encode('utf-8')) > 64 * 1024 * 1024:
        raise ValueError('净值冻结信封超出预算')
    return {'facts': facts, 'publicationRecords': publication, 'responseRaw': response_raw,
            'ruleRaw': rule_evidence.rule_raw, 'calendarRaw': date_evidence.calendar_raw,
            'assumptionRaw': date_evidence.assumption_raw, 'calendarDecisionRaw': date_evidence.decision_raw,
            'calendar': calendar,
            'navVisibility': {'mode': 'research-assumption', 'boundary': 'after-disclosure-day-end',
                              'timezone': 'Asia/Shanghai', 'rule': rule,
                              'disclosureCalendarHash': calendar['contentHash']}}
