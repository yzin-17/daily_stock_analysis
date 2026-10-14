"""东财拆分的有限错误诊断；不输出异常原文、原页、输入值或凭据。"""

from contextlib import contextmanager
import json
import logging
import re
from uuid import UUID

from pydantic import ValidationError
import requests

from src.services.thesis_ledger_event_time_v1 import instant

LOGGER = logging.getLogger(__name__)
STAGES = frozenset({'admission_before', 'evidence_read', 'mapping_preflight', 'query_scope',
                    'source_read', 'source_http', 'source_parse', 'source_pagination',
                    'source_normalize', 'source_result', 'mapping_resolve', 'source_revision',
                    'fact_projection', 'admission_after', 'admission_snapshot',
                    'response_validation', 'wire_validation'})
FIELDS = frozenset({'facts', 'availableAt', 'occurredAt', 'dataAsOf', 'fetchedAt', 'ratio',
                    'recordDate', 'effectiveDate', 'symbol', 'provider', 'providerRevision',
                    'admission', 'coverage', 'dateMappingEvidence', 'identityEvidence',
                    'ref', 'sha256', 'content', 'recordVersion'})


def safe_request_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-fA-F0-9-]{36}', value):
        return None
    try:
        return str(UUID(value))
    except ValueError:
        return None


def safe_failure(stage, error, request_id=None, payload=None):
    kind, status = 'unexpected_error', None
    if isinstance(error, requests.HTTPError):
        kind = 'http_error'
        candidate = getattr(getattr(error, 'response', None), 'status_code', None)
        if type(candidate) is int and 100 <= candidate <= 599:
            status = candidate
    elif isinstance(error, requests.RequestException):
        kind = 'transport_error'
    elif isinstance(error, ValidationError):
        kind = 'validation_error'
    elif isinstance(error, FileNotFoundError):
        kind = 'evidence_missing'
    elif isinstance(error, OSError):
        kind = 'io_error'
    elif isinstance(error, UnicodeError):
        kind = 'decode_error'
    elif isinstance(error, (ValueError, TypeError, KeyError)):
        kind = 'invalid_data'
    record = {'event': 'eastmoney_split_failure', 'stage': stage if stage in STAGES else 'unknown',
              'kind': kind, 'httpStatus': status, 'requestId': safe_request_id(request_id)}
    if isinstance(error, ValidationError):
        errors = error.errors(include_input=False, include_context=False, include_url=False)
        record['validationErrorCount'] = error.error_count()
        record['validationFields'] = sorted({str(part) for item in errors[:4]
                                             for part in item['loc'] if part in FIELDS})
    if isinstance(payload, dict):
        try:
            cutoff = instant(payload['dataAsOf'])
            record['availableAfterCutoffCount'] = sum(instant(fact['availableAt']) > cutoff
                                                      for fact in payload['facts'])
        except (ValueError, TypeError, KeyError):
            record['availableAfterCutoffCount'] = None
    return record


class SplitDiagnostics:
    def __init__(self, request_id=None, emit=None):
        self.request_id = request_id
        self.records = []
        self._seen = set()
        self._emit = emit

    def report(self, stage, error, payload=None):
        if id(error) in self._seen:
            return
        self._seen.add(id(error))
        try:
            record = safe_failure(stage, error, self.request_id, payload)
        except Exception:
            record = {'event': 'eastmoney_split_failure', 'stage': stage if stage in STAGES else 'unknown',
                      'kind': 'diagnostic_unavailable', 'httpStatus': None, 'requestId': None}
        self.records.append(record)
        try:
            if self._emit is not None:
                self._emit(record)
            else:
                LOGGER.warning('%s', json.dumps(record, ensure_ascii=False))
        except Exception:
            # 日志设施失败不能替换原业务异常或触发重试。
            pass


@contextmanager
def diagnostic_stage(report, stage):
    try:
        yield
    except Exception as error:
        if report is not None:
            try:
                report(stage, error)
            except Exception:
                pass
        raise
