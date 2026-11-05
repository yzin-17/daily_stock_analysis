"""同花顺明确年度 EPS 表；参考 a-stock-data §2.2，不回退任意首表。"""

from decimal import Decimal
import io
import re

from bs4 import BeautifulSoup
import pandas as pd
from .market_reference_projection import day, number, require
from .market_reference_public_common import PublicRead, paged, unique


def forecast_tables(content, symbol):
    soup = BeautifulSoup(content.decode('gbk'), 'html.parser')
    title = soup.title.get_text() if soup.title else ''
    require(symbol[:6] in title and '盈利预测' in title, '盈利预测页面不是该证券')
    summary = [table for table in soup.find_all('table') if '汇总--预测年报每股收益' in table.get_text()]
    details = [table for table in soup.find_all('table') if table.find_parent('table') is None
               and '机构名称' in table.get_text() and '报告日期' in table.get_text()
               and '预测年报每股收益（元）' in table.get_text()]
    require(len(summary) == 1 and len(details) == 1, '不能唯一定位 EPS 汇总和机构表')
    require('单位：元' in summary[0].get_text(), 'EPS 表未声明来源元单位')
    return summary[0], details[0]


def parse_consensus(table):
    records = []
    for row in table.find_all('tr'):
        cells = [cell.get_text(strip=True) for cell in row.find_all(['td', 'th'], recursive=False)]
        if not cells or not re.fullmatch(r'\d{4}', cells[0]):
            continue
        require(len(cells) == 6, 'EPS 汇总表的列结构改变')
        institutions = number(cells[1], integer=True)
        minimum, mean, maximum = (number(value) for value in cells[2:5])
        require(institutions is not None and int(institutions) > 0 and all(value is not None for value in (minimum, mean, maximum)), 'EPS 汇总缺少必要数值')
        require(Decimal(minimum) <= Decimal(mean) <= Decimal(maximum), 'EPS 预测范围和均值矛盾')
        records.append({'year': cells[0], 'institutions': institutions, 'minimum': minimum, 'mean': mean, 'maximum': maximum})
    require(bool(records), 'EPS 汇总没有明确年度行')
    unique(records, ('year',))
    return records


def parse_institutions(table):
    frame = pd.read_html(io.StringIO(str(table)), header=[0, 1])[0].fillna('')
    columns = list(frame.columns)
    institution = next((column for column in columns if '机构名称' in column), None)
    analyst = next((column for column in columns if '研究员' in column), None)
    published = next((column for column in columns if '报告日期' in column), None)
    eps_columns = [(column, re.search(r'(\d{4})预测', column[1])) for column in columns if '预测年报每股收益（元）' in column[0]]
    require(institution and analyst and published and eps_columns and all(match for _, match in eps_columns), '机构 EPS 表缺年度、报告日期或来源单位')
    records = []
    for _, row in frame.iterrows():
        require(bool(row[institution]) and bool(row[published]), '机构预测缺少机构或报告日期')
        for column, match in eps_columns:
            value = number(row[column])
            if value is None:
                continue
            records.append({'institution': str(row[institution]), 'analyst': str(row[analyst]) or None,
                            'reportDate': day(str(row[published])), 'year': match.group(1), 'eps': value})
    require(bool(records), '机构预测没有有效 EPS')
    unique(records, ('institution', 'analyst', 'reportDate', 'year'))
    return records


def project_forecasts(content, query):
    summary, details = forecast_tables(content, query.symbol)
    records = parse_consensus(summary) if query.dataset == 'ths-eps-consensus' else parse_institutions(details)
    return paged(query, records, f'https://basic.10jqka.com.cn/{query.symbol[:6]}/worth.html', notes=(
        '按来源表头的预测年度及“元／股”展示；来源未另给 ISO 币种代码，不编造 CNY，不直接作为跨币种估值输入。',
        '汇总数及机构数由来源发布，统计窗口／独立机构去重方法未披露，本系统未自行重算一致预期。机构表仅为页面当前可见报告，不代表全部统计样本或完整历史。'))


def read_forecasts(query, context):
    # 参考路径 /new/ 会重定向到此公开页面；不携带 HiThink API 或本机 netrc 凭据。
    url = f'http://basic.10jqka.com.cn/{query.symbol[:6]}/worth.html'
    content = PublicRead(context.timeout_seconds).read(url, headers={'Referer': 'https://basic.10jqka.com.cn/'})
    return project_forecasts(content, query)
