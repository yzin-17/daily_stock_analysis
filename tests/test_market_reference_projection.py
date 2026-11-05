"""资料投影的币种、单位、日期、零值及来源身份边界。"""

from copy import deepcopy
from types import SimpleNamespace
import pytest
from pydantic import ValidationError

from src.services.thesis_ledger_reference_contract_v1 import ReferenceRequest, ReferenceResult, correlate
from data_provider.market_reference_financial import project_financial
from data_provider.market_reference_events import project_events
from data_provider.market_reference_market import project_market
from data_provider.market_reference_ticks import project_ticks
from data_provider.market_reference_projection import number
from data_provider.market_reference_calendar import project_calendar
from data_provider.market_reference_yields import project_yields


def query(dataset='financial-balance', **changes):
    return ReferenceRequest(**{'requestId': 'reference-test', 'dataset': dataset, 'symbol': '600519.SH', **changes})


def financial():
    return {'result': {'status': {'code': 0}, 'data': {'report_count': 1, 'report_list': {
        '20260930': {'rCurrency': 'CNY', 'rType': '合并期末', 'publish_date': '20261031', 'data': [
            {'item_field': 'TOTASSET', 'item_source': 'fzb', 'item_value': '0'},
            {'item_field': 'TOTLIAB', 'item_source': 'fzb', 'item_value': '-1.25'},
            {'item_field': 'PARESHARRIGH', 'item_source': 'fzb', 'item_value': None},
        ]}}}}}


def test_zero_negative_and_missing_are_distinct_and_identity_is_correlated():
    q = query()
    value = project_financial(financial(), q)
    assert value.items[0].values['assets'] == '0'
    assert value.items[0].values['liabilities'] == '-1.25'
    assert value.items[0].values['parentEquity'] is None
    assert value.items[0].values['publishedDate'] == '2026-10-31'
    assert value.historyComplete is False
    with pytest.raises(ValueError):
        correlate(query(symbol='000001.SZ'), value, {'providerId': 'akshare', 'upstreamSource': 'sina'})


@pytest.mark.parametrize('field,value', [('rCurrency', 'USD'), ('publish_date', '20260901'), ('publish_date', None)])
def test_financial_rejects_unknown_currency_or_disclosure_clock(field, value):
    raw = financial()
    raw['result']['data']['report_list']['20260930'][field] = value
    with pytest.raises(ValueError):
        project_financial(raw, query())


def test_empty_financial_requires_zero_or_out_of_range_source_count():
    raw = financial()
    raw['result']['data']['report_list'] = {}
    with pytest.raises(ValueError):
        project_financial(raw, query())
    raw['result']['data']['report_count'] = 0
    assert project_financial(raw, query()).status == 'empty'


@pytest.mark.parametrize('value', [float('inf'), 'NaN', '1e9999', True, {}, []])
def test_invalid_numbers_are_never_zero(value):
    with pytest.raises(ValueError):
        number(value)


def test_company_event_identity_and_unit_are_not_inferred():
    q = query('earnings-forecast')
    row = {'SECURITY_CODE': '600519', 'SECUCODE': '600519.SH', 'NOTICE_DATE': '2026-10-31',
           'REPORT_DATE': '2026-09-30', 'PREDICT_FINANCE_CODE': '006', 'PREDICT_FINANCE': '营业收入',
           'PREDICT_AMT_LOWER': 0, 'PREDICT_AMT_UPPER': None}
    payload = {'success': True, 'code': 0, 'result': {'count': 1, 'pages': 1, 'data': [row]}}
    assert project_events(payload, q).items[0].values['amountLower'] == '0'
    for patch in [{'SECUCODE': '600519.SZ'}, {'PREDICT_FINANCE_CODE': '001', 'PREDICT_FINANCE': '每股收益'}]:
        bad = deepcopy(payload);bad['result']['data'][0].update(patch)
        with pytest.raises(ValueError):
            project_events(bad, q)


def test_business_error_and_explicit_empty_are_different():
    q = query('institution-survey')
    assert project_events({'success': False, 'code': 9201, 'result': None}, q).status == 'empty'
    with pytest.raises(ValueError):
        project_events({'success': False, 'code': 500, 'result': None}, q)


def test_express_keeps_original_report_period_and_does_not_fill_missing_eps():
    row = {'SECURITY_CODE': '600519', 'SECUCODE': '600519.SH', 'NOTICE_DATE': '2019-07-13',
           'REPORT_DATE': '2019-06-30', 'TOTAL_OPERATE_INCOME': 41200000000, 'PARENT_NETPROFIT': 19900000000,
           'BASIC_EPS': None}
    payload = {'success': True, 'code': 0, 'result': {'data': [row], 'pages': 1, 'count': 1}}
    value = project_events(payload, query('earnings-express'))
    assert value.items[0].values['period'] == '2019-06-30'
    assert value.items[0].values['basicEps'] is None


def test_new_calendar_capability_does_not_leak_to_old_news_source():
    from src.services.thesis_ledger_provider_manifests import PROVIDER_MANIFESTS
    manifest = PROVIDER_MANIFESTS['wallstreetcn']
    sources = {s['sourceId']: s for s in manifest['upstreamSources']}
    assert sources['wallstreetcn-a-stock']['capabilities'] == {'NEWS_FEED': ['STOCK']}
    assert sources['wallstreetcn-macro-calendar']['displayName'] == '华尔街见闻宏观日历'
    assert sources['wallstreetcn-macro-calendar']['capabilities'] == {'REFERENCE_MACRO_CALENDAR': ['STOCK']}


def test_yields_keep_missing_chinese_tenor_separate_from_real_zero():
    payload = {'success': True, 'code': 0, 'result': {'count': 1, 'pages': 1,
        'data': [{'SOLAR_DATE': '2026-09-25', 'EMG00001310': 0}]}}
    q = query('bond-yields', symbol=None, start='2026-09-21', end='2026-09-25')
    value = project_yields(payload, q)
    assert value.items[0].values['china10y'] is None
    assert value.items[0].values['us10y'] == '0'
    payload['result']['data'][0]['EMG00001310'] = None
    with pytest.raises(ValueError):
        project_yields(payload, q)


def test_wrong_pool_day_is_an_error_even_if_empty():
    q = query('limit-up', symbol=None, start='2026-09-25', end='2026-09-25')
    with pytest.raises(ValueError, match='交易日'):
        project_market({'rc': 0, 'data': {'qdate': 20261103, 'tc': 0, 'pool': []}}, q)


def test_calendar_preserves_event_without_original_url_and_zero_value():
    q = query('macro-calendar', symbol=None, start='2026-11-03', end='2026-11-03')
    row = {'id': 1, 'public_date': 1793664000, 'title': '事件', 'uri': '', 'actual': 0}
    payload = {'code': 20000, 'data': {'items': [row, row | {'id': 2, 'title': '指标',
        'uri': 'https://wallstreetcn.com/calendar/test/overview'}]}}
    value = project_calendar(payload, q)
    assert len(value.items) == 2
    assert value.items[0].values['actual'] == '0'
    assert value.items[0].values['originalUrl'] is None


def test_tick_page_and_both_quote_dates_bind_the_session():
    q = query('ticks')
    fields = [''] * 31;fields[2] = '600519';fields[30] = '20261103161453'
    quote = 'v_sh600519="' + '~'.join(fields) + '";'
    frame = 'v_detail_data_sh600519=[0,"0/09:25:02/1450.00/0/1650/239250000/S"]'
    value = project_ticks([frame], quote, quote, q)
    assert value.items[0].values['time'] == '2026-11-03T09:25:02+08:00'
    assert value.items[0].values['quantity'] == '1650'
    with pytest.raises(ValueError):
        project_ticks([frame], quote, quote.replace('20261103', '20261104'), q)
    with pytest.raises(ValueError):
        project_ticks([frame.replace('[0,', '[1,')], quote, quote, q)


@pytest.mark.parametrize('changes', [
    {'dataset': 'report-eps'}, {'dataset': 'industry-ranking', 'symbol': None},
    {'symbol': '600519'}, {'symbol': '510300.SH'}, {'pageSize': 21}, {'requestBudgetMs': True},
    {'cursor': 'fake'}, {'dataset': 'index-members', 'symbol': None, 'indexCode': '399006'},
    {'dataset': 'holder-count', 'start': '2026-01-01'},
    {'dataset': 'global-news', 'symbol': None, 'page': 2},
    {'dataset': 'macro-calendar', 'symbol': None, 'start': '2026-11-01', 'end': '2026-11-09'},
])
def test_query_rejects_unsupported_scope_and_false_history(changes):
    with pytest.raises(ValidationError):
        query(**changes)
