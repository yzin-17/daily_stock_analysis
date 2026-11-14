"""衍生品固定公开协议；参考 a-stock-data Layer 9／13／15。"""

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re

PROFILES = ('future-shfe', 'future-ine', 'future-czce', 'future-cffex', 'future-cffex-xml',
            'future-gfex', 'rank-shfe', 'rank-ine', 'rank-czce', 'rank-cffex',
            'future-quote', 'future-kline', 'a50-quote', 'option-months', 'convertible-bonds', 'cffex-units', 'cffex-unit-page', 'option-unit-rules')
FOLLOW_PROFILES = ('option-chain', 'option-values')
UNDERLYINGS = {'510050': '50ETF', '510300': '300ETF', '588000': '科创50', '510500': '500ETF'}
CB_QUOTES = ('f2~01~CONVERT_STOCK_CODE~CONVERT_STOCK_PRICE,f235~10~SECURITY_CODE~TRANSFER_PRICE,'
             'f236~10~SECURITY_CODE~TRANSFER_VALUE,f2~10~SECURITY_CODE~CURRENT_BOND_PRICE,'
             'f237~10~SECURITY_CODE~TRANSFER_PREMIUM_RATIO')


def request_spec(profile, stamp, code):
    if profile not in PROFILES or date.fromisoformat(stamp).isoformat() != stamp:
        raise ValueError('未登记的衍生品来源或日期')
    ymd = stamp.replace('-', '')
    headers = {'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'}
    params, data, method = {}, None, 'GET'
    if profile in ('future-shfe', 'future-ine', 'rank-shfe', 'rank-ine'):
        host = 'www.ine.cn' if profile.endswith('ine') else 'www.shfe.com.cn'
        kind = 'pm' if profile.startswith('rank-') else 'kx'
        url = f'https://{host}/data/tradedata/future/dailydata/{kind}{ymd}.dat'
    elif profile in ('future-czce', 'rank-czce'):
        name = 'FutureDataHolding' if profile.startswith('rank-') else 'FutureDataDaily'
        url = f'https://www.czce.com.cn/cn/DFSStaticFiles/Future/{ymd[:4]}/{ymd}/{name}.txt'
    elif profile in ('future-cffex', 'future-cffex-xml'):
        name = 'index.xml' if profile.endswith('xml') else f'{ymd}_1.csv'
        url = f'http://www.cffex.com.cn/fzjy/mrhq/{ymd[:6]}/{ymd[6:]}/{name}'
    elif profile == 'rank-cffex':
        if code not in ('IF', 'IH', 'IC', 'IM', 'TS', 'TF', 'T', 'TL'):
            raise ValueError('中金所排名只接受已知品种代码')
        url = f'http://www.cffex.com.cn/sj/ccpm/{ymd[:6]}/{ymd[6:]}/{code}_1.csv'
    elif profile == 'future-gfex':
        url = 'http://www.gfex.com.cn/u/interfacesWebTiDayQuotes/loadList'
        method, data = 'POST', {'trade_date': ymd, 'trade_type': '0'}
        headers['Referer'] = 'http://www.gfex.com.cn/gfex/rihq/hqsj_tjsj.shtml'
    elif profile in ('future-quote', 'future-kline'):
        if not re.fullmatch(r'[A-Z]{1,2}(?:0|\d{2}(?:0[1-9]|1[0-2]))', code):
            raise ValueError('期货代码需要品种加连续零或四位年月')
        headers['Referer'] = 'https://finance.sina.com.cn/'
        if profile == 'future-quote':
            url = 'https://hq.sinajs.cn/list=nf_' + code
        else:
            url = f'https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20_{code}=/InnerFuturesNewService.getDailyKLine'
            params = {'symbol': code}
    elif profile == 'a50-quote':
        if code != 'CHA50CFD':
            raise ValueError('A50 核对固定为来源 CHA50CFD')
        url = 'https://hq.sinajs.cn/list=hf_CHA50CFD'
        headers['Referer'] = 'https://finance.sina.com.cn/'
    elif profile == 'option-months':
        if code not in UNDERLYINGS:
            raise ValueError('ETF 期权标的不在参考实现范围')
        url = 'https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionService.getStockName'
        params = {'exchange': 'null', 'cate': UNDERLYINGS[code]}
        headers['Referer'] = 'https://stock.finance.sina.com.cn/'
    elif profile == 'cffex-units':
        url = 'http://www.cffex.com.cn/rtj/'
    elif profile == 'cffex-unit-page':
        url = 'http://www.cffex.com.cn/cn/rtj.html'
    elif profile == 'option-unit-rules':
        url = 'https://www.sse.com.cn/lawandrules/sselawsrules2025/option/c/10781448/files/0c0f89e74e9c4a5b9aa3a7acafdf5119.docx'
    else:
        url = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
        params = {'reportName': 'RPT_BOND_CB_LIST', 'columns': 'ALL',
                  'sortColumns': 'PUBLIC_START_DATE,SECURITY_CODE', 'sortTypes': '-1,1',
                  'pageNumber': '1', 'pageSize': '20', 'source': 'WEB', 'client': 'WEB',
                  'quoteColumns': CB_QUOTES, 'quoteType': '0'}
    return {'method': method, 'url': url, 'params': params, 'data': data, 'headers': headers}


def parent_body(path, expected):
    p = Path(path)
    receipt = json.loads(p.read_text())
    body = p.with_suffix('.body').read_bytes()
    if (receipt.get('profile') != expected or receipt.get('response', {}).get('httpStatus') != 200
            or receipt['response'].get('truncated') or sha256(body).hexdigest() != receipt['response']['sha256']):
        raise ValueError('父证据类型、响应或摘要不符')
    return receipt, body


def follow_request(profile, parent, stamp, code):
    if date.fromisoformat(stamp).isoformat() != stamp:
        raise ValueError('日期不规范')
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://stock.finance.sina.com.cn/'}
    if profile == 'option-chain':
        match = re.fullmatch(r'(510050|510300|588000|510500):(\d{2}(?:0[1-9]|1[0-2]))', code)
        if not match:
            raise ValueError('合约链需要已知标的及 YYMM')
        receipt, body = parent_body(parent, 'option-months')
        payload = json.loads(body)
        months = payload['result']['data']['contractMonth']
        if payload['result'].get('status', {}).get('code') != 0 or payload['result']['data'].get('stockId') != match[1]:
            raise ValueError('月份接口返回了其他 ETF，不能沿用默认标的')
        if receipt['code'] != match[1] or receipt.get('date') != stamp or match[2] not in [str(m).replace('-', '')[2:] for m in months]:
            raise ValueError('合约月或标的未在父响应中确认')
        query = f'OP_UP_{match[1]}{match[2]},OP_DOWN_{match[1]}{match[2]}'
    elif profile == 'option-values':
        if not re.fullmatch(r'\d{8}', code):
            raise ValueError('期权代码需要八位数字')
        receipt, body = parent_body(parent, 'option-chain')
        codes = re.findall(r'CON_OP_(\d{8})(?=[,\"])', body.decode('gbk'))
        if code not in codes or receipt.get('date') != stamp:
            raise ValueError('期权代码或日期未在父清单中确认')
        query = f'CON_OP_{code},CON_SO_{code}'
    else:
        raise ValueError('未知合约衔接类型')
    return {'method': 'GET', 'url': 'https://hq.sinajs.cn/list=' + query,
            'params': {}, 'data': None, 'headers': headers}
