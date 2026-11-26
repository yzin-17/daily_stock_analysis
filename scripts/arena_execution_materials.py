"""仅从固定的真实官方资料构建两证券离线验收包，不访问网络或生产配置。"""

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts.arena_execution_sources import OfficialSources, ZONE, compact, day_end
from src.services.thesis_ledger_calendar_contract import calendar_contract_response
from src.services.thesis_ledger_historical_reconstruction import DailyExecutionMaterial, reconstruct_context
from data_provider.thesis_ledger_sources.historical_execution import ROOT, daily_context_key

REVIEW = Path(__file__).resolve().parents[1] / 'tests/fixtures/sse-etf-execution-reviewed.json'
HALT_DIGEST = '087735ac94e1b965c175ba5fb3af737546beb399219342c3a78cd40fd9d884a3'
REQUIRED_SOURCES = {'product-588000', 'product-510300', 'trading-notice', 'trading-rules',
                    'deferred-rules', 'exchange-fees', 'clearing-fees', 'stamp-tax',
                    'cash-availability', 'calendar-notice'}


def quote_rows(sources, group, ident, digest, symbol, day):
    raw, receipt = sources.read(group, ident, digest)
    url = urlsplit(receipt['url'])
    params = parse_qs(url.query)
    if (url.hostname != 'yunhq.sse.com.cn' or url.path != '/v1/sh1/mink/' + symbol[:6]
            or params.get('period') != [day.replace('-', '')] or params.get('begin') != ['0']
            or params.get('end') != ['-1']):
        raise ValueError('真实行情代码、日期或范围不符')
    body = json.loads(raw, parse_float=Decimal)
    fields = params['select'][0].split(',')
    if body.get('code') != symbol[:6] or body.get('total') != len(body.get('kline', [])):
        raise ValueError('真实行情身份或条数不符')
    rows = []
    for values in body['kline']:
        if len(values) != len(fields):
            raise ValueError('真实行情字段缺失')
        row = dict(zip(fields, values))
        stamp = datetime.strptime(str(row['date']), '%Y%m%d%H%M%S').replace(tzinfo=ZONE)
        if stamp.date().isoformat() != day or rows and stamp <= rows[-1]['at']:
            raise ValueError('真实行情日期、排序或唯一性无效')
        row['at'] = stamp
        if any(Decimal(str(row[k])) <= 0 for k in ['close', 'high', 'low', 'prevClose']):
            raise ValueError('真实行情价格无效')
        rows.append(row)
    if len(rows) != 241 or rows[-1]['at'].strftime('%H:%M') != '15:00':
        raise ValueError('目标无停牌交易日的官方完整分时资料不足')
    return rows, receipt


def build_materials(source_root, review_path=REVIEW):
    review = json.loads(Path(review_path).read_text())
    if (type(review['version']) is not int or review['version'] != 1 or review['day'] != '2026-11-10'
            or review['asOf'] != '2026-11-10T05:01:00.000Z'
            or {x['symbol'] for x in review['instruments']} != {'588000.SH', '510300.SH'}
            or len(review['instruments']) != 2 or set(review['sources']) != REQUIRED_SOURCES):
        raise ValueError('真实资料验收范围不符')
    sources = OfficialSources(source_root)
    documents = {key: sources.document(key, spec) for key, spec in review['sources'].items()}
    at = datetime.fromisoformat(review['asOf'].replace('Z', '+00:00'))
    window = review['calendarRange']
    calendar = calendar_contract_response(date.fromisoformat(window['start']), date.fromisoformat(window['end']), at)
    if calendar['status'] != 'supported' or not calendar['coverage']['complete']:
        raise ValueError('真实历史可见日历不可用')
    import exchange_calendars as xcals
    exchange = xcals.get_calendar('XSHG')
    day = review['day']
    previous = exchange.previous_session(day).date().isoformat()
    if previous != '2026-11-09' or exchange.next_session(day).date().isoformat() != '2026-11-11':
        raise ValueError('真实前后交易日不符合已核对资料')
    sessions = [
        {'startAt': exchange.session_open(day).isoformat(), 'endAt': exchange.session_break_start(day).isoformat(),
         'evidenceRefs': ['trading-rules', 'calendar-notice']},
        {'startAt': exchange.session_break_end(day).isoformat(), 'endAt': exchange.session_close(day).isoformat(),
         'evidenceRefs': ['trading-rules', 'calendar-notice']},
    ]
    if any(fragment in documents['deferred-rules'][1] for fragment in ['3.3.8', '3.3.11', '3.3.13', '3.3.17', '6.6']):
        raise ValueError('所用制度条款仍暂缓实施')
    materials, observations, audit = {}, {}, {}
    for item in review['instruments']:
        symbol = item['symbol']
        product = documents[item['productRef']][1]
        listed = date.fromisoformat(item['listedOn'])
        if (symbol[:6] not in product and symbol != '510300.SH' or not any(token in product for token in
                [listed.isoformat(), f'{listed.year}年{listed.month}月{listed.day}日'])):
            raise ValueError('上市日期与实际产品原文不符')
        ratio = Decimal(item['ratio'])
        if (symbol == '588000.SH' and (ratio != Decimal('0.20') or '科创板50' not in product)
                or symbol == '510300.SH' and f'涨跌幅比例为{int(ratio * 100)}%' not in product):
            raise ValueError('涨跌幅与产品及有效制度不符')
        prior, previous_receipt = quote_rows(sources, item['priceGroup'], item['priceId'], item['priceSha256'], symbol, previous)
        current, minute_receipt = quote_rows(sources, 'real-minute-fields', item['minuteId'], item['minuteSha256'], symbol, day)
        visible = [row for row in current if row['at'] <= at]
        if not visible or visible[-1]['at'] != at:
            raise ValueError('目标分钟真实记录缺失')
        close = Decimal(str(prior[-1]['close']))
        if close != Decimal(item['previousClose']) or any(Decimal(str(row['prevClose'])) != close for row in visible):
            raise ValueError('前收盘与目标时点已知价格参考不一致')
        tick = Decimal('0.001')
        lower = (close * (1 - ratio)).quantize(tick, rounding=ROUND_HALF_UP)
        upper = (close * (1 + ratio)).quantize(tick, rounding=ROUND_HALF_UP)
        if lower != Decimal(item['lower']) or upper != Decimal(item['upper']):
            raise ValueError('真实限价计算与已审阅值不符')
        ids = [key for key in documents if not key.startswith('product-') or key == item['productRef']]
        evidence = [documents[key][0] for key in ids]
        evidence.append({'id': 'previous-close', 'kind': 'snapshot', 'sha256': item['priceSha256'],
            'reference': previous_receipt['url'], 'availableAt': day_end(previous),
            'observedAt': previous_receipt['observedAt']})
        _, halt = sources.read(item['haltGroup'], item['haltId'], HALT_DIGEST)
        page = {'id': 'official-halts', 'kind': 'reconstruction', 'sha256': HALT_DIGEST,
                'reference': halt['url'], 'httpStatus': halt['status'], 'observedAt': halt['observedAt']}
        revision = 'official-etf-reviewed-v1:' + sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()
        material = {'version': 1, 'day': day, 'fact': {
            'symbol': symbol, 'market': 'CN', 'instrumentType': 'ETF', 'currency': 'CNY',
            'lotSize': '100', 'tickSize': str(tick), 'tradable': True,
            'provider': 'historical-execution-archive', 'providerRevision': revision,
            'occurredAt': sessions[0]['startAt'], 'availableAt': max(e['availableAt'] for e in evidence),
            'executionRules': {'status': 'supported', 'version': revision, 'range': {'start': day, 'end': day},
                'price': {'reference': 'previousClose', 'maxUpRatio': str(ratio), 'maxDownRatio': str(ratio)},
                'positionSettlement': {'sellableAfterTradingDays': 1},
                'cashSettlement': {'buyDebitAfterTradingDays': 0, 'sellCreditAfterTradingDays': 0},
                'statutoryCharges': [{'code': 'sseFundHandling', 'side': 'both', 'rate': '0.00004', 'minimum': None}]}},
            'priceRule': {'kind': 'exact', 'currency': 'CNY', 'lower': str(lower), 'upper': str(upper)},
            'ruleCoverage': {k: True for k in ['quantity', 'price', 'positionSettlement', 'cashSettlement', 'statutoryCharges']},
            'ruleEvidenceRefs': ids, 'evidence': evidence,
            'listing': {'listedOn': listed.isoformat(), 'delistedOn': None, 'evidenceRefs': [item['productRef']]},
            'sessions': sessions, 'pages': [page]}
        validated = DailyExecutionMaterial.model_validate(material)
        reconstruct_context(validated, review['asOf'], sources.raw.__getitem__)
        materials[symbol] = material
        points = [{'timestamp': row['at'].isoformat(), 'availableAt': row['at'].isoformat(),
            **{key: float(row[key]) for key in ['open', 'high', 'low', 'close', 'volume', 'amount']},
            'completionStatus': 'complete'} for row in visible]
        observations[symbol] = {'name': item['name'], 'points': points, 'source': minute_receipt,
            'seriesVersion': 'official-sse-minutes:' + item['minuteSha256']}
        audit[symbol] = {'listedOn': item['listedOn'], 'previousTradingDate': previous,
            'previousClose': str(close), 'priceLimits': [str(lower), str(upper)],
            'visibleRows': len(visible), 'excludedFutureRows': len(current) - len(visible),
            'availability': {'documents': '发布日期与当前字节修改日期取较晚日末，不冒充精确发布时间',
                'previousClose': '仅引用上一交易日完整原文，以该日末作保守可见上界；未给当日全天原文回填可见时刻'},
            'fees': {'handlingRate': '0.00004', 'handlingPayer': '会员等；账户佣金含费仍单独核对',
                'excluded': ['ETF份额证券交易印花税', '股票证管费', '申赎组合证券过户费', '结算参与人风险基金', '基金管理费与托管费'],
                'minimum': None, 'brokerCommission': '未读取、修改或假定实际账户佣金是否含经手费'}}
    return {'materials': materials, 'observations': observations, 'calendar': calendar,
            'audit': audit, 'originals': sources.raw, 'review': review}


def write_package(package, output):
    output = Path(output).resolve()
    production = ROOT.resolve()
    if output == production or production in output.parents or output in production.parents:
        raise ValueError('离线入口禁止写入生产历史执行目录')
    output.mkdir(parents=False, exist_ok=False, mode=0o700)
    (output / 'daily').mkdir()
    (output / 'originals').mkdir()
    index = {'version': 2, 'contexts': {}, 'dailyContexts': {}}
    for symbol, material in package['materials'].items():
        key = daily_context_key(symbol, 'ETF', material['day'])
        raw = json.dumps(material, ensure_ascii=False).encode()
        (output / 'daily' / (key + '.json')).write_bytes(raw)
        index['dailyContexts'][key] = {'assetType': 'ETF', 'sha256': sha256(raw).hexdigest()}
    for digest, raw in package['originals'].items():
        (output / 'originals' / digest).write_bytes(raw)
    for name, value in [('index', index), ('calendar', package['calendar']), ('observations', package['observations']),
                        ('audit', package['audit']), ('review', package['review'])]:
        (output / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2))
