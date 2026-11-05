"""腾讯聚合分笔；用前后行情时间确认交易日，不使用宿主机日期补齐。"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import re
import time

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from .market_reference_projection import http_bytes, number, require, result

ENDPOINT = 'https://stock.gtimg.cn/data/index.php'
ZONE = timezone(timedelta(hours=8))


def source_symbol(query):
    return query.symbol[-2:].lower() + query.symbol[:6]


def quote_time(raw, query):
    match = re.fullmatch(r'\s*v_' + source_symbol(query) + r'="([^"\r\n]*)";?\s*', raw)
    require(match is not None, '分笔校时行情身份不符')
    fields = match[1].split('~')
    require(len(fields) > 30 and fields[2] == query.symbol[:6], '分笔校时证券不符')
    require(re.fullmatch(r'\d{14}', fields[30]) is not None, '行情未提供可确认的交易日')
    return datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=ZONE)


def source_pages(query):
    begin = (query.page - 1) * query.pageSize
    return range(begin // 70, (begin + query.pageSize - 1) // 70 + 1)


def project_ticks(frames, before, after, query):
    first, last = quote_time(before, query), quote_time(after, query)
    require(first.date() == last.date() and first <= last, '分笔读取期间交易日变化或行情时钟倒退')
    records, has_next = [], False
    for page, raw in zip(source_pages(query), frames, strict=True):
        match = re.fullmatch(r'\s*v_detail_data_' + source_symbol(query) + r'=(\[.*\]);?\s*', raw, re.S)
        require(match is not None, '分笔封套或证券身份不符')
        payload = json.loads(match[1])
        require(isinstance(payload, list) and len(payload) == 2 and type(payload[0]) is int
                and payload[0] == page and isinstance(payload[1], str), '来源分笔页不符')
        rows = payload[1].split('|') if payload[1] else []
        require(len(rows) <= 70, '来源分笔页超过协议边界')
        for offset, line in enumerate(rows):
            parts = line.split('/')
            require(len(parts) == 7 and parts[6] in {'B', 'S', 'M'}, '分笔字段或方向无效')
            sequence = number(parts[0], True)
            require(sequence == str(page * 70 + offset), '分笔序号不连续或页不符')
            stamp = datetime.fromisoformat(first.date().isoformat() + 'T' + parts[1]).replace(tzinfo=ZONE)
            require(stamp <= last, '分笔成交时间晚于来源行情时钟')
            price, quantity, amount = number(parts[2]), number(parts[4], True), number(parts[5])
            require(price is not None and quantity is not None and amount is not None
                    and Decimal(price) > 0 and Decimal(quantity) >= 0 and Decimal(amount) >= 0, '分笔价格量额无效')
            records.append({'time': stamp.isoformat(), 'sequence': sequence, 'price': price, 'quantity': quantity,
                            'amount': amount, 'side': {'B': '买盘 B', 'S': '卖盘 S', 'M': '中性 M'}[parts[6]]})
        has_next = len(rows) == 70
    start = (query.page - 1) * query.pageSize - source_pages(query).start * 70
    selected = records[start:start + query.pageSize]
    return result(query, 'tencent', 'tencent', 'tencent-aggregated-ticks-v1',
                  'https://gu.qq.com/' + source_symbol(query), selected,
                  has_more=start + len(selected) < len(records) or has_next,
                  notes=('最近来源交易日的约三秒聚合分笔，成交量单位为手；不等同于交易所逐笔或 Level 2。',
                         '交易日由读取前后同一证券行情时间确认；未声明整日完整性。'))


def read_ticks(query, context):
    deadline = time.monotonic() + context.timeout_seconds
    headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'}
    def read(url, params):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CapabilityContractError('timeout', '分笔读取预算耗尽')
        return http_bytes(url, params, headers, remaining).decode('gb18030')
    before = read('https://qt.gtimg.cn/q=' + source_symbol(query), {})
    frames = [read(ENDPOINT, {'appn': 'detail', 'action': 'data', 'c': source_symbol(query), 'p': str(page)})
              for page in source_pages(query)]
    after = read('https://qt.gtimg.cn/q=' + source_symbol(query), {})
    return project_ticks(frames, before, after, query)
