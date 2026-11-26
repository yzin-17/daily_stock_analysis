"""研报元数据：以真实字段形状验证单页预算、来源身份及最小保存边界。"""

import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from data_provider import eastmoney_report_metadata as source
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_report_metadata_contract import CONTRACT, ReportMetadataRequest


def query(**changes):
    return ReportMetadataRequest(**{
        'symbol': '600519.SH', 'requestId': 'local-metadata-test',
        'start': '2026-08-01', 'end': '2026-10-23',
        'page': 1, 'pageSize': 2, 'requestBudgetMs': 3000, **changes,
    })


def payload():
    return {'hits': 772, 'size': 2, 'TotalPage': 386, 'pageNo': 1, 'data': [
        {'infoCode': 'AP202609211829719676', 'stockCode': '600519', 'title': '公司跟踪报告',
         'orgSName': '诚通证券', 'publishDate': '2026-09-21 00:00:00.000', 'predictThisYearEps': '99',
         'abstract': '不得保存的正文或摘要'},
        {'infoCode': 'AP202608211828244348', 'stockCode': '600519', 'title': '中报点评',
         'orgSName': '西南证券', 'publishDate': '2026-08-21 00:00:00.000'},
    ]}


def test_minimal_projection_keeps_date_precision_and_stable_original_link():
    result = source.project_page(payload(), query(), '2026-10-22T16:27:04Z')
    item = result.items[0]
    assert item.reference == 'eastmoney:report:AP202609211829719676'
    assert item.originalUrl == 'https://pdf.dfcfw.com/pdf/H3_AP202609211829719676_1.pdf'
    assert item.publishedDate == '2026-09-21' and item.datePrecision == 'date'
    assert result.fetchedAt != item.publishedDate and result.historyComplete is False
    saved = result.model_dump_json()
    assert '99' not in saved and 'abstract' not in saved and '不得保存' not in saved


@pytest.mark.parametrize('field,value', [('stockCode', '000001'), ('publishDate', '2026-02-30'),
    ('publishDate', None), ('infoCode', '../other'), ('orgSName', ''), ('title', None)])
def test_invalid_source_record_fails_the_page(field, value):
    data = payload()
    data['data'][0][field] = value
    with pytest.raises(CapabilityContractError, match='校验失败'):
        source.project_page(data, query(), '2026-10-22T16:27:04Z')


@pytest.mark.parametrize('field,value', [('hits', True), ('TotalPage', 385), ('size', 100), ('pageNo', 2)])
def test_inconsistent_paging_fails_without_partial_success(field, value):
    data = payload()
    data[field] = value
    with pytest.raises(CapabilityContractError):
        source.project_page(data, query(), '2026-10-22T16:27:04Z')


def test_missing_or_duplicate_rows_do_not_claim_a_complete_page():
    for rows in [payload()['data'][:1], [payload()['data'][0]] * 2]:
        data = {**payload(), 'data': rows}
        with pytest.raises(CapabilityContractError):
            source.project_page(data, query(), '2026-10-22T16:27:04Z')


def test_window_and_target_are_correlated():
    with pytest.raises(CapabilityContractError):
        source.project_page(payload(), query(start='2026-09-01'), '2026-10-22T16:27:04Z')
    result = source.project_page(payload(), query(), '2026-10-22T16:27:04Z')
    with pytest.raises(ValueError):
        CONTRACT.correlate_result(query(), result, {'providerId': 'other', 'upstreamSource': 'eastmoney'})


def test_empty_source_is_an_observed_empty_page_without_history_claim():
    data = {'hits': 0, 'size': 2, 'TotalPage': 0, 'pageNo': 1, 'data': []}
    result = source.project_page(data, query(), '2026-10-22T16:27:04Z')
    assert result.items == () and result.historyComplete is False


@pytest.mark.parametrize('changes', [{'symbol': '600519'}, {'symbol': '600519.BJ'}, {'instrument_type': 'ETF'},
    {'page': 0}, {'pageSize': 21}, {'requestBudgetMs': True}, {'start': '2026-11-01'}])
def test_request_rejects_unbounded_or_incompatible_inputs(changes):
    with pytest.raises(ValidationError):
        query(**changes)


class Response:
    status = 200

    def __init__(self, body):
        self.body = body
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size):
        self.read_sizes.append(size)
        return self.body


def test_transport_issues_exactly_one_bounded_list_request():
    response = Response(json.dumps(payload()).encode())
    with patch.object(source, 'open_list', return_value=response) as fetch:
        source.read_page(query(), ReadContext(None, timeout_seconds=1.5))
    assert fetch.call_count == 1 and fetch.call_args.kwargs['timeout'] == 1.5
    request = fetch.call_args.args[0]
    assert request.full_url.startswith(source.ENDPOINT + '?')
    assert 'pageSize=2' in request.full_url and 'code=600519' in request.full_url
    assert response.read_sizes == [source.MAX_RESPONSE_BYTES + 1]


@pytest.mark.parametrize('error,code', [(TimeoutError(), 'timeout'), (URLError('blocked'), 'upstream_failure'),
    (ConnectionResetError(), 'upstream_failure'),
    (HTTPError(source.ENDPOINT, 302, 'redirect', {}, None), 'upstream_failure'),
    (HTTPError(source.ENDPOINT, 403, 'forbidden', {}, None), 'permission_denied'),
    (HTTPError(source.ENDPOINT, 429, 'limited', {}, None), 'rate_limited')])
def test_transport_failure_does_not_retry_or_switch_source(error, code):
    with patch.object(source, 'open_list', side_effect=error) as fetch:
        with pytest.raises(CapabilityContractError) as caught:
            source.read_page(query(), ReadContext(None))
    assert caught.value.code == code and fetch.call_count == 1


def test_transport_diagnostics_keep_status_and_request_id_without_raw_error(caplog):
    private = 'synthetic-private-error-body'
    errors = [HTTPError(source.ENDPOINT, 500, private, {'Authorization': private}, None),
              URLError(private), ConnectionResetError(private)]
    for error in errors:
        with patch.object(source, 'open_list', side_effect=error) as fetch:
            with pytest.raises(CapabilityContractError):
                source.read_page(query(), ReadContext(None))
        assert fetch.call_count == 1
    assert 'requestId=local-metadata-test' in caplog.text and 'httpStatus=500' in caplog.text
    assert 'errorType=ConnectionResetError' in caplog.text
    assert private not in caplog.text and source.ENDPOINT not in caplog.text


@pytest.mark.parametrize('body', [b'x' * (source.MAX_RESPONSE_BYTES + 1), b'{invalid'])
def test_response_limits_and_json_are_checked(body):
    with patch.object(source, 'open_list', return_value=Response(body)) as fetch:
        with pytest.raises(CapabilityContractError) as caught:
            source.read_page(query(), ReadContext(None))
    assert caught.value.code == 'invalid_response' and fetch.call_count == 1


def test_new_capability_uses_exact_existing_registration():
    from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
    binding = CAPABILITY_SOURCES.resolve(query().route_key(), {'providerId': 'akshare', 'upstreamSource': 'eastmoney'})
    assert binding is source.BINDING and binding.admission_required is False
    assert CAPABILITY_SOURCES.resolve(query().route_key(), {'providerId': 'akshare', 'upstreamSource': 'sina'}) is None


@pytest.mark.parametrize('start,end', [(None, None), ('2026-08-01', None), (None, '2026-10-23')])
def test_optional_filters_do_not_add_hidden_dates(start, end):
    response = Response(json.dumps(payload()).encode())
    with patch.object(source, 'open_list', return_value=response) as fetch:
        result = source.read_page(query(start=start, end=end), ReadContext(None))
    params = parse_qs(urlsplit(fetch.call_args.args[0].full_url).query, keep_blank_values=True)
    assert params['beginTime'] == [start or '']
    assert params['endTime'] == [end or '']
    assert result.start == start and result.end == end
    assert result.metadataVersion == 'market-external-report-metadata-v1'
    assert fetch.call_count == 1 and result.historyComplete is False


@pytest.mark.parametrize('start,end', [('2026-09-01', None), (None, '2026-08-01')])
def test_single_date_filter_is_still_enforced_on_source_results(start, end):
    with pytest.raises(CapabilityContractError):
        source.project_page(payload(), query(start=start, end=end), '2026-10-22T16:27:04Z')


def test_named_metadata_contract_accepts_omitted_date_filters():
    values = {'symbol': '000001.SZ', 'requestId': 'no-date', 'page': 1, 'pageSize': 2, 'requestBudgetMs': 3000}
    request = ReportMetadataRequest(**values)
    assert request.start is None and request.end is None
    with pytest.raises(ValidationError):
        ReportMetadataRequest(**values, start='2026-09-01', end='2026-08-01')
