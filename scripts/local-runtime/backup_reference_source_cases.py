"""首批备用源的固定公开读取协议；不改变产品来源选择。"""

from datetime import date
import re

FINANCIAL = {
    'backup-ths-debt': 'debt',
    'backup-ths-benefit': 'benefit',
    'backup-ths-cash': 'cash',
}
PROFILES = (*FINANCIAL, 'backup-margin-sh', 'backup-margin-sz',
            'backup-dragon-sh', 'backup-dragon-sz', 'backup-sina-reports')


def request_spec(profile, stamp, code):
    if profile not in PROFILES or date.fromisoformat(stamp).isoformat() != stamp:
        raise ValueError('备用来源或日期无效')
    if not re.fullmatch(r'(?:60\d{4}|688\d{3}|00\d{4}|30\d{4})', code):
        raise ValueError('备用源仅核对规范沪深 A 股代码')
    if profile.endswith('-sh') and not code.startswith(('60', '688')):
        raise ValueError('上交所采证代码不属于沪市')
    if profile.endswith('-sz') and not code.startswith(('00', '30')):
        raise ValueError('深交所采证代码不属于深市')
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
    params = {}
    if profile in FINANCIAL:
        url = f'https://basic.10jqka.com.cn/api/stock/finance/{code}_{FINANCIAL[profile]}.json'
        headers['Referer'] = 'https://basic.10jqka.com.cn/'
    elif profile == 'backup-margin-sh':
        url = 'https://query.sse.com.cn/marketdata/tradedata/queryMargin.do'
        headers['Referer'] = 'https://www.sse.com.cn/'
        params = {'isPagination': 'true', 'tabType': 'mxtype', 'detailsDate': stamp.replace('-', ''),
                  'pageHelp.pageSize': '5000', 'pageHelp.pageNo': '1', 'pageHelp.beginPage': '1',
                  'pageHelp.cacheSize': '1', 'pageHelp.endPage': '1'}
    elif profile == 'backup-margin-sz':
        url = 'https://www.szse.cn/api/report/ShowReport'
        headers['Referer'] = 'https://www.szse.cn/'
        params = {'SHOWTYPE': 'xlsx', 'CATALOGID': '1837_xxpl', 'TABKEY': 'tab2', 'txtDate': stamp}
    elif profile == 'backup-dragon-sh':
        url = 'https://query.sse.com.cn/infodisplay/showTradePublicFile.do'
        headers['Referer'] = 'https://www.sse.com.cn/disclosure/diclosure/public/'
        params = {'jsonCallBack': 'cb', 'isPagination': 'false', 'dateTx': stamp}
    elif profile == 'backup-dragon-sz':
        url = 'https://www.szse.cn/api/report/ShowReport/data'
        headers['Referer'] = 'https://www.szse.cn/disclosure/supervision/dealinfo/index.html'
        params = {'SHOWTYPE': 'JSON', 'CATALOGID': '1842_xxpl', 'TABKEY': 'tab1',
                  'txtStart': stamp, 'txtEnd': stamp, 'random': '0.9'}
    else:
        url = 'https://vip.stock.finance.sina.com.cn/q/go.php/vReport_List/kind/search/index.phtml'
        headers['Referer'] = 'https://finance.sina.com.cn/'
        params = {'symbol': code, 't1': 'all', 'p': '1'}
    return {'method': 'GET', 'url': url, 'params': params, 'data': None, 'headers': headers}
