"""三表备用资格的公司数据块、量纲、精度与缺值反例。"""

import json
from copy import deepcopy

import pytest

from data_provider.reference_backup_financial import FIELDS, displayed_number, parse_financial


def sample(kind='debt'):
    titles = ['科目\\时间'] + [[labels[0], '元'] for labels in FIELDS[kind].values()]
    report = [['2026-09-30', '2026-06-30']] + [['1.23亿', '0'] for _ in FIELDS[kind]]
    table = {'title': titles, 'report': report}
    alternate = deepcopy(table)
    alternate['report'][1][0] = '9.99亿'
    return {'flashData': json.dumps(table), 'fieldflashData': json.dumps(alternate)}


@pytest.mark.parametrize('kind', ['debt', 'benefit', 'cash'])
def test_company_report_is_separate_from_other_data_blocks(kind):
    result = parse_financial(sample(kind), kind)
    key = next(iter(FIELDS[kind]))
    assert result['records'][0][key] == '123000000.00'
    assert result['records'][0]['roundingHalfUnits'][key] == '500000.00'
    assert result['records'][0]['currency'] is None
    assert result['records'][0]['publishedDate'] is None
    assert result['directReplacement'] is False


@pytest.mark.parametrize('value,expected', [('0', '0'), ('-2.50万', '-25000.00'), ('1.23', '1.23'), (False, None), ('', None)])
def test_scaled_display_values_keep_zero_negative_and_missing_distinct(value, expected):
    assert displayed_number(value)[0] == expected


@pytest.mark.parametrize('value', [True, 1, '1.23%', '未知', 'NaN'])
def test_unknown_numeric_or_quantity_layout_is_rejected(value):
    with pytest.raises(ValueError):
        displayed_number(value)


@pytest.mark.parametrize('mutation', ['unit', 'length', 'duplicate-period', 'missing-core'])
def test_core_layout_changes_fail_instead_of_guessing(mutation):
    payload = sample()
    table = json.loads(payload['flashData'])
    if mutation == 'unit':
        table['title'][1][1] = '%'
    elif mutation == 'length':
        table['report'][1].pop()
    elif mutation == 'duplicate-period':
        table['report'][0][1] = table['report'][0][0]
    else:
        table['title'][1][0] = '其他指标'
    payload['flashData'] = json.dumps(table)
    with pytest.raises(ValueError):
        parse_financial(payload, 'debt')
