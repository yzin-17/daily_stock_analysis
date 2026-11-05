"""央行社融和统计局 PMI；参考 a-stock-data §11，按官方页面定位附件。"""

import io
import re

from bs4 import BeautifulSoup
import pandas as pd
from .market_reference_projection import day, number, require
from .market_reference_public_common import PublicRead, paged, same_publisher_link, unique

PBC = 'https://www.pbc.gov.cn/diaochatongjisi/116219/116319/index.html'
NBS = 'https://www.stats.gov.cn/sj/zxfb/'


def links(content):
    return [(a.get_text(strip=True), a['href']) for a in BeautifulSoup(content, 'html.parser').find_all('a', href=True)]


def parse_social(content):
    frame = pd.read_excel(io.BytesIO(content), header=None, dtype=str).fillna('')
    text = ' '.join(str(cell) for row in frame.head(11).values for cell in row)
    require('社会融资规模增量统计表' in text and '亿元人民币' in text, '央行附件不是人民币社融增量表')
    header = next((index for index, row in frame.iterrows() if str(row.iloc[0]).strip() == '月份'), None)
    require(header is not None, '央行社融附件没有月份表头')
    mapping = {'total': '社会融资规模增量', 'rmbLoans': '人民币贷款', 'fxLoansRmb': '外币贷款（折合人民币）',
               'corporateBonds': '企业债券', 'governmentBonds': '政府债券', 'equity': '非金融企业境内股票融资'}
    fields = [re.sub(r'\s+', '', str(value)) for value in frame.iloc[header]]
    require(set(mapping.values()) <= set(fields), '央行社融科目列改变')
    records = []
    for _, row in frame.iloc[header + 1:].iterrows():
        matched = re.fullmatch(r'(\d{4})\.(\d{2})', str(row.iloc[0]).strip())
        if not matched:
            continue
        period = matched.group(1) + '-' + matched.group(2)
        require(1 <= int(matched.group(2)) <= 12, '央行社融月份无效')
        values = {key: number(row.iloc[fields.index(label)]) for key, label in mapping.items()}
        if all(value is None for value in values.values()):
            continue
        require(values['total'] is not None, '央行社融总量缺失')
        records.append({'period': period, **values})
    require(bool(records), '央行社融附件没有月度记录')
    unique(records, ('period',))
    return sorted(records, key=lambda row: row['period'], reverse=True)


def read_social(query, context):
    read = PublicRead(context.timeout_seconds)
    years = [(int(name[:4]), href) for name, href in links(read.read(PBC)) if re.fullmatch(r'\d{4}年统计数据', name)]
    require(bool(years), '央行索引没有年度链接')
    year_url = same_publisher_link(PBC, max(years)[1], 'www.pbc.gov.cn')
    topic = next((href for name, href in links(read.read(year_url)) if name == '社会融资规模'), None)
    require(topic is not None, '央行年度页没有社融专题')
    topic_url = same_publisher_link(year_url, topic, 'www.pbc.gov.cn')
    book = next((href for _, href in links(read.read(topic_url)) if re.search(r'\.xlsx?(?:\?|$)', href, re.I)), None)
    require(book is not None, '央行社融专题没有附件')
    url = same_publisher_link(topic_url, book, 'www.pbc.gov.cn')
    return paged(query, parse_social(read.read(url)), url, notes=(
        '官方最新年度附件的月度增量，单位为亿元人民币；累计行与存量表不混入。',
        '附件可能已修订，没有逐月初次公布的严格历史可见性；抓取时间不替代发布时间，不声称全历史。'))


def parse_pmi(content):
    soup = BeautifulSoup(content, 'html.parser')
    title = soup.title.get_text(strip=True) if soup.title else ''
    period = re.search(r'(\d{4})年(\d{1,2})月中国采购经理指数运行情况', title)
    require(period is not None, '统计局页面不是明确月份的 PMI 公布')
    for tag in soup(['script', 'style']):
        tag.decompose()
    text = re.sub(r'\s+', '', soup.get_text())
    patterns = {'manufacturing': r'(?<!非)制造业采购经理指数[（(]PMI[）)]为(-?\d+(?:\.\d+)?)%',
                'nonManufacturing': r'非制造业商务活动指数为(-?\d+(?:\.\d+)?)%',
                'composite': r'综合PMI产出指数为(-?\d+(?:\.\d+)?)%'}
    values = {}
    for key, pattern in patterns.items():
        matched = re.search(pattern, text)
        require(matched is not None, '统计局 PMI 核心指标未能定位')
        values[key] = number(matched.group(1))
    published = soup.find('meta', attrs={'name': re.compile(r'^PubDate$', re.I)})
    date_value = day(published['content'][:10].replace('/', '-')) if published and published.get('content') else None
    return [{'period': f'{int(period.group(1)):04d}-{int(period.group(2)):02d}', 'publishedDate': date_value, **values}]


def read_pmi(query, context):
    read = PublicRead(context.timeout_seconds)
    href = next((href for name, href in links(read.read(NBS)) if '采购经理指数' in name), None)
    require(href is not None, '统计局最新发布页没有 PMI 条目')
    url = same_publisher_link(NBS, href, 'www.stats.gov.cn')
    return paged(query, parse_pmi(read.read(url)), url, notes=(
        '国家统计局最新月度公布；统计月份与来源发布日期分开，不从 URL 推测缺失的发布时间。',
        '展示来源指标值，不将月频 PMI 当作每日数据或自动策略信号。'))
