"""问答、来源预测、编辑元数据及官方宏观表格的语义边界。"""

import io
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pandas as pd
import pytest

from data_provider.market_reference_public_editorial import parse_cctv, parse_themes
from data_provider.market_reference_public_forecasts import parse_consensus, forecast_tables
from data_provider.market_reference_public_qna import parse_sse_qna, parse_szse_qna
from data_provider.market_reference_public_macro import parse_social, parse_pmi
from data_provider.market_reference_public_curves import parse_curves


def test_consensus_accepts_year_in_header_cell_and_preserves_negative_eps():
    table = BeautifulSoup('<table><tr><th>2026</th><td>3</td><td>-2</td><td>-1</td><td>0</td><td>1</td></tr></table>', 'html.parser').table
    assert parse_consensus(table) == [{'year': '2026', 'institutions': '3', 'minimum': '-2', 'mean': '-1', 'maximum': '0'}]


def test_eps_does_not_fallback_to_first_unrelated_table():
    body = '<title>测试公司(600519) 盈利预测</title><table><tr><td>净利润</td></tr></table>'.encode('gbk')
    with pytest.raises(ValueError):
        forecast_tables(body, '600519.SH')


def test_consensus_does_not_accept_mean_outside_source_range():
    table = BeautifulSoup('<table><tr><th>2026</th><td>3</td><td>1</td><td>4</td><td>3</td><td>1</td></tr></table>', 'html.parser').table
    with pytest.raises(ValueError):
        parse_consensus(table)


def sse_question():
    return '''<div class="m_feed_item" id="item-12"><div class="m_feed_detail m_qa_detail">
    <div class="m_feed_txt"><a>:测试公司(600000)</a>业务问题</div>
    <div class="m_feed_from"><span>昨天 18:22</span></div></div></div>'''


def test_qna_preserves_unanswered_and_original_relative_time():
    result = parse_sse_qna(sse_question().encode())
    assert result[0]['answer'] is None
    assert result[0]['askedTime'] == '昨天 18:22'
    assert result[0]['symbol'] == '600000.SH'


@pytest.mark.parametrize('body', ['', '<html>错误页</html>', sse_question() * 2])
def test_qna_does_not_convert_invalid_page_or_duplicates_into_empty(body):
    with pytest.raises(ValueError):
        parse_sse_qna(body.encode())


def test_szse_keeps_unanswered_time_separate_and_validates_security():
    row = {'stockCode': '002594', 'companyShortName': '测试公司', 'indexId': '1234567890123456789',
           'mainContent': '问题', 'pubDate': 1793656576000, 'attachedContent': None, 'attachedPubDate': None}
    query = SimpleNamespace(symbol='002594.SZ', page=1, pageSize=5)
    payload = {'rows': [row], 'total': 1, 'pageNo': 1, 'pageSize': 5}
    result = parse_szse_qna(payload, query)
    assert result[0]['answer'] is None and result[0]['answeredTime'] is None
    assert result[0]['askedTime'].endswith('+08:00')
    row['stockCode'] = '000001'
    with pytest.raises(ValueError):
        parse_szse_qna(payload, query)


def test_cctv_keeps_program_distinct_and_rejects_other_day_links():
    body = '<li><a title="《新闻联播》 20261103 19:00" href="https://tv.cctv.com/2026/11/03/VIDEabc.shtml"></a></li>'
    assert parse_cctv(body.encode(), '2026-11-03')[0]['kind'] == '整期节目'
    with pytest.raises(ValueError):
        parse_cctv(body.encode(), '2026-11-02')


def test_editorial_reason_is_source_text_and_requires_same_date():
    payload = {'errocode': 0, 'date': '2026-11-03', 'data': [{'date': '2026-11-03', 'code': '600000', 'name': '公司', 'reason': '编辑标签'}]}
    assert parse_themes(payload, '2026-11-03')[0]['reason'] == '编辑标签'
    with pytest.raises(ValueError):
        parse_themes(payload, '2026-11-02')


def social_excel():
    rows = [['社会融资规模增量统计表', '', '', '', '', '', ''],
            ['单位：亿元人民币', '', '', '', '', '', ''],
            ['月份', '社会融资规模增量', '人民币贷款', '外币贷款（折合人民币）', '企业债券', '政府债券', '非金融企业境内股票融资'],
            ['2026.01', '1', '-2', '0', '3', '', '5'], ['2026.02', '', '', '', '', '', '']]
    output = io.BytesIO()
    pd.DataFrame(rows).to_excel(output, index=False, header=False)
    return output.getvalue()


def test_social_does_not_invent_future_months_or_zero_fill_unknown_component():
    records = parse_social(social_excel())
    assert len(records) == 1 and records[0]['period'] == '2026-01'
    assert records[0]['governmentBonds'] is None
    assert records[0]['rmbLoans'] == '-2' and records[0]['fxLoansRmb'] == '0'


def test_nbs_publication_slash_date_is_not_guessed_from_url():
    html = '''<title>2026年10月中国采购经理指数运行情况 - 国家统计局</title>
    <meta name="PubDate" content="2026/10/31 09:30:00">
    制造业采购经理指数（PMI）为49.3%，非制造业商务活动指数为49.7%，综合PMI产出指数为49.7%'''
    result = parse_pmi(html.encode())[0]
    assert result['publishedDate'] == '2026-10-31' and result['period'] == '2026-10'


def curves_html(omit=None):
    names = ['中债国债收益率曲线', '中债商业银行普通债收益率曲线(AAA)', '中债中短期票据收益率曲线(AAA)']
    header = '<tr>' + ''.join(f'<th>{name}</th>' for name in ['曲线名称', '日期', '3月', '6月', '1年', '3年', '5年', '7年', '10年', '30年']) + '</tr>'
    rows = []
    for name in names:
        if name == omit:
            continue
        values = [name, '2026-11-03', *(['1.5'] * 7), '-' if '中短期' in name else '2.0']
        rows.append('<tr>' + ''.join(f'<td>{value}</td>' for value in values) + '</tr>')
    return ('<table>' + header + ''.join(rows) + '</table>').encode()


def test_curves_preserve_absent_tenor_but_reject_missing_whole_curve():
    query = SimpleNamespace(start='2026-11-03', end='2026-11-03')
    records = parse_curves(curves_html(), query)
    assert next(row for row in records if '中短期' in row['curve'])['thirtyYears'] is None
    with pytest.raises(ValueError):
        parse_curves(curves_html('中债国债收益率曲线'), query)
