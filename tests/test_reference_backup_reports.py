"""研报备用列表的证券证明、日期粒度、链接与记录完整性。"""

import pytest

from data_provider.reference_backup_reports import parse_reports


def page(*, symbol='600519', day='2026-11-03', host='stock.finance.sina.com.cn', sequence=1):
    headers = ''.join('<th>' + text + '</th>' for text in ['序号', '标题', '报告类型', '发布日期', '机构', '研究员'])
    row = f'<tr><td>{sequence}</td><td><a title="公司({symbol})：研究" href="//{host}/stock/go.php/vReport_Show/kind/search/rptid/123/index.phtml">标题</a></td><td>公司</td><td>{day}</td><td>机构</td><td>研究员</td></tr>'
    return ('<table><tr>' + headers + '</tr>' + row + '</table>').encode('gbk')


def test_metadata_does_not_fabricate_rating_target_pdf_or_exact_time():
    result = parse_reports(page(), '600519', as_of='2026-11-04')
    assert result['count'] == 1 and not result['directReplacement']
    row = result['records'][0]
    assert row['rating'] is None and row['targetPrice'] is None and row['pdfUrl'] is None
    assert row['publishedDate'] == '2026-11-03'
    assert result['publicationPrecision'] == '日期，未提供精确时间'


@pytest.mark.parametrize('kwargs', [{'symbol': '000001'}, {'day': '2026-11-05'}, {'host': 'example.com'}, {'sequence': 2}])
def test_wrong_security_future_date_external_link_or_missing_row_is_rejected(kwargs):
    with pytest.raises(ValueError):
        parse_reports(page(**kwargs), '600519', as_of='2026-11-04')


def test_empty_page_is_not_proof_that_the_security_has_no_reports():
    raw = page().decode('gbk')
    raw = raw[:raw.index('<tr><td>')] + '</table>'
    with pytest.raises(ValueError, match='未取得实际记录'):
        parse_reports(raw.encode('gbk'), '600519', as_of='2026-11-04')
