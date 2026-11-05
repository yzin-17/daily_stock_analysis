"""东财可转债条款及来源最新转股字段；参考 a-stock-data Layer 15。"""

from datetime import datetime
import re

from .market_reference_projection import day, number, require
from .market_reference_derivatives_common import PublicRead, CN, direct_result, unique

URL = 'https://datacenter-web.eastmoney.com/api/data/v1/get'
QUOTES = ('f2~01~CONVERT_STOCK_CODE~CONVERT_STOCK_PRICE,f235~10~SECURITY_CODE~TRANSFER_PRICE,'
          'f236~10~SECURITY_CODE~TRANSFER_VALUE,f2~10~SECURITY_CODE~CURRENT_BOND_PRICE,'
          'f237~10~SECURITY_CODE~TRANSFER_PREMIUM_RATIO')


def listing_status(row, today):
    listed, removed = day(row.get('LISTING_DATE')), day(row.get('DELIST_DATE'))
    market = row.get('TRADE_MARKET')
    if (removed is not None and removed <= today) or market == 'STAS00':
        return '已摘牌'
    if market not in ('CNSESH', 'CNSESZ'):
        return '未知'
    if listed is not None and listed <= today:
        return '已上市'
    if listed is not None or day(row.get('PUBLIC_START_DATE')) is not None:
        return '待上市'
    return '未知'


def parse_convertible(payload, query, today):
    require(isinstance(payload, dict) and payload.get('success') is True and payload.get('code') == 0,
            '可转债接口没有明确成功')
    result = payload.get('result')
    require(isinstance(result, dict) and isinstance(result.get('data'), list), '可转债封套改变')
    total, pages, rows = result.get('count'), result.get('pages'), result['data']
    require(type(total) is int and total > 0 and type(pages) is int and pages == (total + query.pageSize - 1) // query.pageSize,
            '可转债总数或分页不符')
    expected = min(query.pageSize, max(0, total - (query.page - 1) * query.pageSize))
    require(len(rows) == expected and all(isinstance(row, dict) for row in rows), '可转债当前页不完整或页大小未生效')
    records = []
    date_fields = {'applyDate': 'PUBLIC_START_DATE', 'listingDate': 'LISTING_DATE', 'delistDate': 'DELIST_DATE',
                   'expireDate': 'EXPIRE_DATE', 'convertStart': 'TRANSFER_START_DATE'}
    number_fields = {'issueSize': 'ACTUAL_ISSUE_SCALE', 'initialConvertPrice': 'INITIAL_TRANSFER_PRICE',
                     'convertPrice': 'TRANSFER_PRICE', 'bondPrice': 'CURRENT_BOND_PRICE', 'stockPrice': 'CONVERT_STOCK_PRICE',
                     'convertValue': 'TRANSFER_VALUE', 'premiumPct': 'TRANSFER_PREMIUM_RATIO'}
    for row in rows:
        required = {'SECURITY_CODE', 'SECURITY_NAME_ABBR', 'CONVERT_STOCK_CODE', 'TRADE_MARKET',
                    *date_fields.values(), *number_fields.values()}
        require(required <= row.keys(), '可转债关键条款或报价字段被移除，不能当作来源空值')
        require(isinstance(row.get('SECURITY_CODE'), str) and re.fullmatch(r'\d{6}', row['SECURITY_CODE'])
                and row.get('SECURITY_NAME_ABBR'), '可转债代码或名称缺失')
        stock_code = row.get('CONVERT_STOCK_CODE')
        require(stock_code is None or re.fullmatch(r'\d{6}', stock_code), '可转债正股代码格式改变')
        values = {key: day(row.get(source)) for key, source in date_fields.items()}
        values.update({key: number(row.get(source)) for key, source in number_fields.items()})
        records.append({'code': row['SECURITY_CODE'], 'name': row['SECURITY_NAME_ABBR'],
                        'status': listing_status(row, today), 'stockCode': stock_code,
                        'stockName': row.get('SECURITY_SHORT_NAME') or None, 'rating': row.get('RATING') or None,
                        'applyCode': row.get('CORRECODE') or None, **values})
    unique(records, ('code',))
    return records, total


def read_convertible(query, context):
    params = {'reportName': 'RPT_BOND_CB_LIST', 'columns': 'ALL', 'sortColumns': 'PUBLIC_START_DATE,SECURITY_CODE',
              'sortTypes': '-1,1', 'pageNumber': str(query.page), 'pageSize': str(query.pageSize),
              'source': 'WEB', 'client': 'WEB', 'quoteColumns': QUOTES, 'quoteType': '0'}
    payload = PublicRead(context.timeout_seconds).json(URL, params)
    today = datetime.now(CN).date().isoformat()
    records, total = parse_convertible(payload, query, today)
    return direct_result(query, records, URL + '?reportName=RPT_BOND_CB_LIST', total=total,
                         has_more=query.page * query.pageSize < total, notes=(
        '来源当前分页，包含待上市、已上市、已摘牌与未知状态，不扫描全表；上市状态按北京时间及来源日期／市场判断，未来摘牌日不提前视为已摘牌。',
        '行情及转股价／转股价值／溢价率由来源填写，可能异步更新；缺失保持为空，已上市不等于当前可交易，不重新计算或推断停牌。',
        '发行规模为亿元，价格按来源元展示；债价与每股转股价的计量对象不同，不将债价当成每股价格。抓取时间不冒充每个报价字段的更新时间。'))
