"""替代目录查询保留原生身份，缺类型和北交所不能被当成完整覆盖。"""
from types import SimpleNamespace
from contextlib import nullcontext
from data_provider.baostock_history_catalog import read_history_catalog, catalog_summary


def result(fields, rows):
    cursor = iter(rows)
    state = SimpleNamespace(error_code='0', fields=fields)
    def next_row():
        state.row = next(cursor, None)
        return state.row is not None
    state.next = next_row
    state.get_row_data = lambda: state.row
    return state


def test_native_membership_does_not_invent_missing_etf_or_bj_coverage():
    sdk = SimpleNamespace(query_all_stock=lambda **kw: result(['code'], [['sh.600000'], ['sz.000001']]),
        query_stock_basic=lambda: result(['code', 'type', 'ipoDate', 'outDate'], [['sh.600000', '1', '2000-01-01', '']]))
    raw = read_history_catalog(SimpleNamespace(_baostock_session=lambda: nullcontext(sdk)), '2026-11-02')
    summary = catalog_summary(raw)
    assert summary['exchanges'] == {'sh': 1, 'sz': 1}
    assert summary['missingTypes'] == 1 and '5' not in summary['types'] and 'bj' not in summary['exchanges']
