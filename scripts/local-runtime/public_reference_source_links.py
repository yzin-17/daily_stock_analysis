"""仅从已核对公开回执解析同一发布机构的下一层固定页面。"""

from hashlib import sha256
from datetime import date
import json
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

FOLLOW_PROFILES = {'pboc-year': 'pboc-index', 'pboc-topic': 'pboc-year',
                   'pboc-excel': 'pboc-topic', 'nbs-article': 'nbs-index',
                   'szse-qna': 'szse-org', 'ths-consensus-page': 'ths-consensus'}


def checked_parent(profile, path):
    receipt = json.loads(Path(path).read_text())
    if receipt['profile'] != FOLLOW_PROFILES[profile]:
        raise ValueError('父回执类型不符')
    response = receipt.get('response') or {}
    body = Path(path).with_suffix('.body').read_bytes()
    if response.get('sha256') != sha256(body).hexdigest() or response.get('truncated'):
        raise ValueError('父响应原件缺失、被修改或截断')
    expected_status = 301 if profile == 'ths-consensus-page' else 200
    if response.get('httpStatus') != expected_status:
        raise ValueError('父回执未达到本次链接核对前提')
    return receipt, body


def follow_request(profile, path, day, code):
    from bs4 import BeautifulSoup

    if not re.fullmatch(r'\d{6}', code) or date.fromisoformat(day).isoformat() != day:
        raise ValueError('后续查询的日期或代码无效')
    receipt, body = checked_parent(profile, path)
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
    params, data, method = {}, None, 'GET'
    if profile == 'szse-qna':
        payload = json.loads(body)
        matches = [row for row in payload.get('data', []) if row.get('stockCode') == code]
        if len(matches) != 1 or not re.fullmatch(r'[a-zA-Z0-9]+', matches[0].get('secid', '')):
            raise ValueError('机构定位没有精确唯一证券身份')
        url = 'https://irm.cninfo.com.cn/newircs/company/question'
        method = 'POST'
        params = {'_t': '1', 'stockcode': code, 'orgId': matches[0]['secid'], 'pageSize': '5',
                  'pageNum': '1', 'keyWord': '', 'startDay': '', 'endDay': ''}
    elif profile == 'ths-consensus-page':
        url = receipt['response']['location']
        target = urlparse(url)
        if target.scheme not in ('http', 'https') or target.username or target.password or target.hostname != 'basic.10jqka.com.cn' or target.path != f'/{code}/worth.html' or target.query:
            raise ValueError('同花顺跳转未指向该证券的固定页面')
        headers['Referer'] = 'https://basic.10jqka.com.cn/'
    else:
        soup = BeautifulSoup(body, 'html.parser')
        links = [(a.get_text(strip=True), a['href']) for a in soup.find_all('a', href=True)]
        if profile == 'pboc-year':
            choices = [(int(name[:4]), href) for name, href in links if re.fullmatch(r'\d{4}年统计数据', name)]
            href = max(choices)[1]
        elif profile == 'pboc-topic':
            href = next(href for name, href in links if name == '社会融资规模')
        elif profile == 'pboc-excel':
            href = next(href for _, href in links if re.search(r'\.xlsx?(?:\?|$)', href, re.I))
        else:
            href = next(href for name, href in links if '采购经理指数' in name)
        url = urljoin(receipt['response']['url'], href)
        host = 'www.stats.gov.cn' if profile == 'nbs-article' else 'www.pbc.gov.cn'
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != host or parsed.username or parsed.password:
            raise ValueError('下一层必须仍为已核对发布机构的 HTTPS 页面')
    return {'method': method, 'url': url, 'params': params, 'data': data, 'headers': headers}
