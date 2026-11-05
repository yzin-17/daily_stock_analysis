"""中债三类收益率曲线；参考 a-stock-data §11.3 的表头和适用起点。"""

from bs4 import BeautifulSoup
import re

from .market_reference_projection import day, number, require
from .market_reference_public_common import PublicRead, paged, unique

URL = 'https://yield.chinabond.com.cn/cbweb-pbc-web/pbc/historyQuery'
HEADER = ['曲线名称', '日期', '3月', '6月', '1年', '3年', '5年', '7年', '10年', '30年']
START = {'中债国债收益率曲线': '2006-03-01', '中债中短期票据收益率曲线(AAA)': '2006-12-25',
         '中债商业银行普通债收益率曲线(AAA)': '2009-12-24'}
FIELDS = ('threeMonths', 'sixMonths', 'oneYear', 'threeYears', 'fiveYears', 'sevenYears', 'tenYears', 'thirtyYears')


def parse_curves(content, query):
    soup = BeautifulSoup(content, 'html.parser')
    candidates = []
    for table in soup.find_all('table'):
        rows = [[re.sub(r'\s+', '', cell.get_text()) for cell in row.find_all(['th', 'td'])] for row in table.find_all('tr')]
        if rows and rows[0] == HEADER:
            candidates.append(rows)
    require(len(candidates) == 1, '中债收益率表头改变或不唯一')
    records, observed = [], {}
    for cells in candidates[0][1:]:
        require(len(cells) == len(HEADER), '中债曲线列数改变')
        curve = cells[0].replace('（', '(').replace('）', ')')
        stamp = day(cells[1])
        require(curve in START and stamp and query.start <= stamp <= query.end and stamp >= START[curve], '中债曲线身份或日期不符')
        values = {key: number(raw) for key, raw in zip(FIELDS, cells[2:])}
        required = FIELDS[:-1] if curve == '中债中短期票据收益率曲线(AAA)' else FIELDS
        require(all(values[key] is not None for key in required), '中债曲线缺少已声明期限')
        if curve == '中债中短期票据收益率曲线(AAA)':
            require(values['thirtyYears'] is None, '中短票来源出现未声明的三十年期限')
        records.append({'date': stamp, 'curve': curve, **values})
        observed.setdefault(stamp, set()).add(curve)
    unique(records, ('date', 'curve'))
    for stamp, names in observed.items():
        require(names == {name for name, first in START.items() if stamp >= first}, '中债同日曲线不完整')
    return sorted(records, key=lambda row: (row['date'], row['curve']), reverse=True)


def read_curves(query, context):
    params = {'startDate': query.start, 'endDate': query.end, 'gjqx': '0', 'qxId': 'ycqx', 'locale': 'cn_ZH'}
    content = PublicRead(context.timeout_seconds).read(URL, params)
    return paged(query, parse_curves(content, query), URL, notes=(
        '中债国债、商业银行普通债 AAA 和中短期票据 AAA 曲线，单位为百分数；中短票没有三十年期限，保留为空。',
        '每次窗口最多 360 天，核对已返回日期的应有曲线；没有逐交易日对账，不以整天无记录证明市场休市或历史完整。'))
