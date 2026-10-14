"""东财拆分诊断的脱敏、失败阶段与零网络回归。"""

from contextlib import contextmanager
import json
import socket

from pydantic import ValidationError, create_model
import pytest
import requests

from data_provider import eastmoney_fund_split_reader as native
from data_provider.eastmoney_split_diagnostics import SplitDiagnostics, diagnostic_stage, safe_failure
from src.services.thesis_ledger_event_v3 import EventV3Error, execute_event_request
from tests.test_eastmoney_fund_split_reader import ROW, page
from tests.test_thesis_ledger_split_event_control_v3 import setup as control_setup

setup = control_setup
SECRET = 'private-token-fixture'
REQUEST_ID = 'ed92afdb-5f4b-4d6c-8f95-d8acf8dc08cf'


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    attempts = []

    def forbidden(*args, **kwargs):
        attempts.append(True)
        raise AssertionError('此验证禁止实源网络')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(requests.Session, 'request', forbidden)
    yield
    assert attempts == []


def http_error(status):
    response = requests.Response()
    response.status_code = status
    response._content = SECRET.encode()
    response.url = f'https://example.invalid/?token={SECRET}'
    response.headers['Authorization'] = SECRET
    return requests.HTTPError(SECRET, response=response)


def log_records(caplog):
    records = [json.loads(record.getMessage()) for record in caplog.records
               if record.name == 'data_provider.eastmoney_split_diagnostics']
    assert SECRET not in json.dumps(records)
    return records


@pytest.mark.parametrize('status', [403, 503])
def test_http_status_is_evidence_without_raw_body_or_payment_inference(status):
    record = safe_failure('source_http', http_error(status), REQUEST_ID)
    assert record == {'event': 'eastmoney_split_failure', 'stage': 'source_http',
                      'kind': 'http_error', 'httpStatus': status, 'requestId': REQUEST_ID}
    assert SECRET not in json.dumps(record)


def test_validation_locations_and_correlation_are_allowlisted():
    model = create_model('SensitiveFixture', ratio=(int, ...), private_field=(int, ...))
    with pytest.raises(ValidationError) as failure:
        model.model_validate({'ratio': SECRET, 'private_field': SECRET})
    record = safe_failure(SECRET, failure.value, SECRET)
    assert record['stage'] == 'unknown' and record['requestId'] is None
    assert record['validationFields'] == ['ratio'] and record['validationErrorCount'] == 2
    assert SECRET not in json.dumps(record) and 'private_field' not in json.dumps(record)


@pytest.mark.parametrize('error', [requests.Timeout(SECRET), requests.ConnectionError(SECRET)])
def test_transport_class_does_not_expose_exception_message(error):
    assert safe_failure('source_http', error)['kind'] == 'transport_error'
    assert SECRET not in json.dumps(safe_failure('source_http', error))


def test_nested_failure_is_logged_once_and_reraises_original():
    records = []
    diagnostics = SplitDiagnostics(REQUEST_ID, records.append)
    error = http_error(503)
    with pytest.raises(requests.HTTPError) as failure:
        with diagnostic_stage(diagnostics.report, 'source_read'):
            with diagnostic_stage(diagnostics.report, 'source_http'):
                raise error
    assert failure.value is error and len(records) == 1
    assert records[0]['stage'] == 'source_http'


def test_logging_failure_does_not_replace_primary_error():
    def unavailable(*args):
        raise RuntimeError(SECRET)

    diagnostics = SplitDiagnostics(REQUEST_ID, unavailable)
    error = ValueError('原业务失败')
    with pytest.raises(ValueError) as failure:
        with diagnostic_stage(diagnostics.report, 'source_parse'):
            raise error
    assert failure.value is error and len(diagnostics.records) == 1
    with pytest.raises(ValueError) as failure:
        with diagnostic_stage(unavailable, 'source_parse'):
            raise error
    assert failure.value is error


@contextmanager
def upstream_failure(caplog):
    with pytest.raises(EventV3Error) as failure:
        yield
    assert failure.value.code == 'upstream_failure'
    assert len(log_records(caplog)) == 1


@pytest.mark.parametrize('stage', ['source_http', 'source_parse', 'source_pagination', 'source_normalize'])
def test_native_failure_keeps_original_code_and_request_budget(setup, monkeypatch, caplog, stage):
    runtime, request, _, _ = setup
    calls = []

    def read_page(session, **options):
        calls.append(options['page'])
        if stage == 'source_http':
            raise http_error(503)
        if stage == 'source_parse':
            return SECRET
        if stage == 'source_pagination':
            return page([ROW], (2, 1, options['page']))
        return page([ROW])

    monkeypatch.setattr(native, 'read_event_page', read_page)
    if stage == 'source_normalize':
        def invalid_normalization(*args, **kwargs):
            raise ValueError(SECRET)
        monkeypatch.setattr(native, 'normalize_eastmoney_fund_splits', invalid_normalization)
    with upstream_failure(caplog):
        execute_event_request(runtime, request)
    record = log_records(caplog)[0]
    assert record['stage'] == stage
    assert record['httpStatus'] == (503 if stage == 'source_http' else None)
    assert calls == ([1, 2] if stage == 'source_pagination' else [1])


@pytest.mark.parametrize('missing', [True, False])
def test_local_evidence_rejection_happens_before_source(setup, caplog, missing):
    runtime, request, reader, path = setup
    if missing:
        path.unlink()
    else:
        path.write_bytes(b'{}')
    with upstream_failure(caplog):
        execute_event_request(runtime, request, reader=reader)
    assert log_records(caplog)[0]['stage'] == 'evidence_read'
    reader.assert_not_called()


def test_mapping_failure_is_distinct_from_source_http(setup, caplog):
    runtime, request, reader, _ = setup
    reader.return_value['observations'][0]['sourceRatioPerUnit'] = '3'
    with upstream_failure(caplog):
        execute_event_request(runtime, request, reader=reader)
    assert log_records(caplog)[0]['stage'] == 'mapping_resolve'
    reader.assert_called_once()


def test_success_keeps_observation_proof_and_coverage_without_failure_log(setup, caplog):
    runtime, request, reader, path = setup
    result = execute_event_request(runtime, request, reader=reader)
    assert log_records(caplog) == []
    assert result['facts'][0]['availableAt'] == reader.return_value['observations'][0]['observedAt']
    assert result['dateMappingEvidence']['content'].encode() == path.read_bytes()
    assert result['coverage'] == {'complete': False, 'reason': 'historical_coverage_unverified'}
    reader.assert_called_once_with(request['symbol'], start='2025-10-17', end='2025-10-20')


def test_response_time_failure_is_sanitized_and_remains_invalid_response(setup, monkeypatch, caplog):
    from data_provider import eastmoney_split_capability as source
    runtime, request, reader, _ = setup
    original = source.read_mapped_split_events

    def observed_after_cutoff(*args, **kwargs):
        raw = original(*args, **kwargs)
        raw['facts'][0]['availableAt'] = '2099-01-01T00:00:00.400Z'
        return raw

    monkeypatch.setattr(source, 'read_mapped_split_events', observed_after_cutoff)
    with pytest.raises(EventV3Error) as failure:
        execute_event_request(runtime, request, reader=reader)
    assert failure.value.code == 'invalid_response'
    record = log_records(caplog)[0]
    assert record['stage'] == 'response_validation' and record['kind'] == 'validation_error'
    assert record['availableAfterCutoffCount'] == 1
    assert '2099' not in json.dumps(record)
    reader.assert_called_once()
