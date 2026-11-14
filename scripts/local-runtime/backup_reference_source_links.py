"""仅跟随已校验原件的固定分页、详情和新浪迁移地址。"""

from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from backup_reference_source_cases import request_spec

FOLLOW_PROFILES = ('backup-margin-sh-page', 'backup-margin-sz-meta',
                   'backup-dragon-sz-detail', 'backup-sina-reports-follow')


def follow_request(profile, parent, stamp, code):
    if profile not in FOLLOW_PROFILES:
        raise ValueError('未登记的备用后续采证')
    initial = {'backup-margin-sh-page': 'backup-margin-sh',
               'backup-margin-sz-meta': 'backup-margin-sz',
               'backup-dragon-sz-detail': 'backup-dragon-sz',
               'backup-sina-reports-follow': 'backup-sina-reports'}[profile]
    base = request_spec(initial, stamp, code)
    path = Path(parent)
    receipt = json.loads(path.read_text())
    body = path.with_suffix('.body').read_bytes()
    response = receipt.get('response') or {}
    if receipt.get('profile') != initial or receipt.get('date') != stamp:
        raise ValueError('父回执类型或日期不符')
    if response.get('sha256') != sha256(body).hexdigest() or response.get('truncated') is not False:
        raise ValueError('父原件摘要变化或不完整')
    expected = request_spec(initial, stamp, receipt.get('code', ''))
    if receipt.get('request') != expected:
        raise ValueError('父回执请求身份不符')
    source = urlparse(response.get('url', ''))
    if source.scheme != 'https' or source.netloc != urlparse(base['url']).netloc or source.path != urlparse(base['url']).path:
        raise ValueError('父回执不属于固定来源')
    if profile == 'backup-sina-reports-follow':
        location = urlparse(response.get('location') or '')
        if response.get('httpStatus') != 302 or location.scheme not in ('http', 'https'):
            raise ValueError('新浪父响应没有明确迁移')
        if location.netloc != 'stock.finance.sina.com.cn' or location.path != '/stock/go.php/vReport_List/kind/search/index.phtml':
            raise ValueError('新浪迁移目标不在白名单')
        if parse_qs(location.query) != {'symbol': [code], 't1': ['all'], 'p': ['1']}:
            raise ValueError('新浪迁移查询身份变化')
        base['url'] = 'https://stock.finance.sina.com.cn' + location.path
        return base
    if response.get('httpStatus') != 200:
        raise ValueError('父响应没有成功取得原件')
    if profile == 'backup-margin-sh-page':
        if receipt.get('code') != code:
            raise ValueError('两融分页代码变化')
        payload = json.loads(body)
        page = payload['pageHelp']
        if payload.get('detailsDate') != stamp.replace('-', '') or page.get('pageNo') != 1:
            raise ValueError('上交所分页日期或当前页不符')
        if page.get('pageCount') != 2 or len(page.get('data', [])) != page.get('pageSize'):
            raise ValueError('当前资格样本只接受已证实的两页结构')
        if not page['pageSize'] < page.get('total', 0) <= page['pageSize'] * 2:
            raise ValueError('上交所分页总数不符')
        base['params'].update({'pageHelp.pageNo': '2', 'pageHelp.beginPage': '2',
                               'pageHelp.endPage': '2', 'pageHelp.pageSize': str(page['pageSize'])})
        return base
    if profile == 'backup-margin-sz-meta':
        if receipt.get('code') != code or not body.startswith(b'PK'):
            raise ValueError('深交所父响应不是同证券的 Excel 原件')
        base['url'] += '/data'
        base['params']['SHOWTYPE'] = 'JSON'
        base['params']['PAGENO'] = '1'
        return base
    payload = json.loads(body)
    if not isinstance(payload, list) or len(payload) != 1 or payload[0]['metadata'].get('catalogid') != '1842_xxpl':
        raise ValueError('深交所龙虎榜目录不符')
    matches = [row for row in payload[0]['data'] if row.get('zqdm') == code and row.get('dqrq') == stamp]
    if len(matches) != 1:
        raise ValueError('必须选父页中唯一的证券／原因行')
    anchors = BeautifulSoup(matches[0].get('bz', ''), 'html.parser').find_all('a', attrs={'a-param': True})
    if len(anchors) != 1:
        raise ValueError('来源未给唯一详情链接')
    link = urlparse(anchors[0]['a-param'])
    params = parse_qs(link.query)
    expected_keys = {'SHOWTYPE', 'CATALOGID', 'TABKEY', 'DQRQ', 'ZQDM', 'ZBDM'}
    if link.scheme or link.netloc or link.path != '/ShowReport/data' or set(params) != expected_keys:
        raise ValueError('详情链接不属于固定协议')
    required = {'SHOWTYPE': ['JSON'], 'CATALOGID': ['1842_detal'], 'TABKEY': ['tab1,tab2'],
                'DQRQ': [stamp], 'ZQDM': [code]}
    if any(params.get(key) != value for key, value in required.items()) or not re.fullmatch(r'\d{4}', params['ZBDM'][0]):
        raise ValueError('详情链接证券、日期或原因身份不符')
    base['params'] = {key: value[0] for key, value in params.items()}
    return base
