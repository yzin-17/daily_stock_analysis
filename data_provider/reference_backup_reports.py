"""新浪单证券研报列表资格解析，保留日期粒度及元数据缺口。"""

from datetime import date
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .market_reference_projection import require


def parse_reports(content, symbol, *, as_of):
    require(re.fullmatch(r'(?:60\d{4}|688\d{3}|00\d{4}|30\d{4})', symbol) is not None, '研报证券代码无效')
    date.fromisoformat(as_of)
    soup = BeautifulSoup(content.decode('gbk'), 'html.parser')
    columns = ['序号', '标题', '报告类型', '发布日期', '机构', '研究员']
    tables = [table for table in soup.find_all('table')
              if [head.get_text(' ', strip=True) for head in table.find_all('th')] == columns]
    require(len(tables) == 1, '新浪研报表头不唯一或结构变化')
    records, ids = [], set()
    for row in tables[0].find_all('tr'):
        cells = row.find_all('td', recursive=False)
        if not cells or not cells[0].get_text(strip=True).isdigit():
            continue
        require(len(cells) == 6, '新浪研报记录列数变化')
        sequence = int(cells[0].get_text(strip=True))
        require(sequence == len(records) + 1, '新浪研报记录序号不连续')
        link = cells[1].find('a', attrs={'title': True, 'href': True})
        require(link is not None, '新浪研报缺少标题或链接')
        title = link['title'].strip()
        require(re.search(r'[（(]' + re.escape(symbol) + r'[）)]', title) is not None, '新浪研报标题证券与查询不符')
        url = urljoin('https://stock.finance.sina.com.cn/', link['href'])
        parsed = urlparse(url)
        identity = re.fullmatch(r'/stock/go.php/vReport_Show/kind/search/rptid/(\d+)/index.phtml', parsed.path)
        require(parsed.scheme in ('http', 'https') and parsed.netloc == 'stock.finance.sina.com.cn'
                and identity is not None and not parsed.query, '新浪研报链接不属于已核对来源')
        report_id = identity[1]
        require(report_id not in ids, '新浪研报 ID 重复')
        ids.add(report_id)
        published = cells[3].get_text(strip=True)
        require(date.fromisoformat(published).isoformat() == published and published <= as_of, '新浪研报发布日期无效或在未来')
        records.append({'id': report_id, 'symbol': symbol, 'title': title,
                        'reportType': cells[2].get_text(strip=True), 'publishedDate': published,
                        'institution': cells[4].get_text(' ', strip=True) or None,
                        'authors': cells[5].get_text(' ', strip=True) or None,
                        'url': 'https://stock.finance.sina.com.cn' + parsed.path,
                        'rating': None, 'targetPrice': None, 'pdfUrl': None})
    require(bool(records), '新浪研报本次代表切片未取得实际记录；空页不能证明没有研报')
    return {'records': records, 'count': len(records), 'page': 1, 'directReplacement': False,
            'scope': '单证券当前一页元数据', 'publicationPrecision': '日期，未提供精确时间',
            'currentMetadataRowFields': ['sourceReportId', 'reference', 'title', 'publisher', 'publishedDate', 'datePrecision', 'originalUrl'],
            'currentPageContractGaps': ['exactTotalItems', 'exactTotalPages', 'pageSizeControl', 'dateWindowSemantics'],
            'fullResearchFeatureGaps': ['rating', 'targetPrice', 'pdfUrl', 'completeHistory'],
            'identityEvidence': '逐条标题回显证券代码，报告 ID 和链接唯一'}
