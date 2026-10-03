"""目录页的计数、身份与非执行式解析边界。"""

import json

import pytest

from data_provider.eastmoney_fund_catalog_page import parse_fund_catalog_page


def body(**changes):
    fields = dict(datas=['000001,基金甲,rest', '000002,基金乙,rest'], allRecords=3,
                  pageIndex=1, pageNum=2, allPages=2, allNum=3)
    fields.update(changes)
    return 'var rankData = {' + ','.join(f'{k}:{json.dumps(v, ensure_ascii=False)}' for k, v in fields.items()) + '};'


def test_full_and_final_page_counts():
    first = parse_fund_catalog_page(body(), page=1, page_size=2)
    assert first.rows == (('000001', '基金甲'), ('000002', '基金乙'))
    final = parse_fund_catalog_page(body(pageIndex=2, datas=['000003,基金丙,rest']), page=2, page_size=2)
    assert (final.total, final.pages, len(final.rows)) == (3, 2, 1)


@pytest.mark.parametrize('changes', [
    {'allPages': 1}, {'pageIndex': 2}, {'pageNum': 3}, {'allRecords': True},
    {'datas': ['000001,基金甲,rest']}, {'datas': ['000001,基金甲,rest'] * 2},
    {'datas': ['000001,,rest', '000002,基金乙,rest']}, {'allRecords': 0},
])
def test_incomplete_or_inconsistent_pages_rejected(changes):
    with pytest.raises(ValueError):
        parse_fund_catalog_page(body(**changes), page=1, page_size=2)


@pytest.mark.parametrize('mutate', [
    lambda value: value + 'alert(1);',
    lambda value: value.replace('allNum:3', 'allNum:3,allNum:3'),
    lambda value: value.replace('allNum:3', 'allNum:calculate()'),
    lambda value: value.replace('allNum:3', 'unknown:3'),
])
def test_script_expressions_duplicate_fields_and_unknown_format_rejected(mutate):
    with pytest.raises(ValueError):
        parse_fund_catalog_page(mutate(body()), page=1, page_size=2)
