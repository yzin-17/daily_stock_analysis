"""通达信官网盘后包；布局参考 a-stock-data §1.3 及其 tdx2db 引用。"""

import io
import math
import re
import struct
import zipfile
import zlib

from .market_reference_projection import number, require
from .market_reference_public_common import PublicRead, paged

BASE = 'https://www.tdx.com.cn/products/data/data/g4day/'
MIN_PRICED = {'sh': 10000, 'sz': 3000, 'bj': 50}
MAX_UNCOMPRESSED = 64 * 1024 * 1024


def asset_kind(code, market):
    if market == 'sh' and re.fullmatch(r'(?:60\d{4}|688\d{3})', code):
        return '股票'
    if market == 'sz' and re.fullmatch(r'(?:00\d{4}|30\d{4})', code):
        return '股票'
    if market == 'bj' and re.fullmatch(r'(?:[48]\d{5}|92\d{4})', code):
        return '股票'
    if (market == 'sh' and re.fullmatch(r'5[168]\d{4}', code)) or (market == 'sz' and re.fullmatch(r'15\d{4}', code)):
        return 'ETF'
    return None


def parse_market(codes, blocks, market, stamp):
    require(len(codes) % 150 == 0 and len(blocks) % 512 == 0 and len(codes) // 150 == len(blocks) // 512,
            '盘后包代码表与行情块不完整')
    records, seen_codes, seen_sequences, priced = [], set(), set(), 0
    for offset in range(0, len(codes), 150):
        source = codes[offset:offset + 150]
        code = source[:6].decode('ascii')
        sequence = struct.unpack('<H', source[32:34])[0]
        require(bool(re.fullmatch(r'\d{6}', code)) and code not in seen_codes and sequence not in seen_sequences,
                '盘后包代码或行情块序号重复／损坏')
        seen_codes.add(code)
        seen_sequences.add(sequence)
        block = blocks[sequence * 512:(sequence + 1) * 512]
        require(len(block) == 512, '盘后行情块越界')
        previous = struct.unpack('<d', block[4:12])[0]
        prices = struct.unpack('<4d', block[12:44])
        amount = struct.unpack('<d', block[72:80])[0]
        require(all(math.isfinite(value) for value in (previous, *prices, amount)), '盘后包数值非有限')
        if prices[3] <= 0:
            continue
        priced += 1
        kind = asset_kind(code, market)
        if kind is None:
            continue
        name = source[40:72].split(b'\x00')[0].decode('gbk').strip()
        require(bool(name), '盘后证券名称缺失')
        values = {field: number(round(value, 4)) for field, value in zip(('open', 'high', 'low', 'close'), prices)}
        records.append({'date': stamp, 'symbol': code + '.' + market.upper(), 'name': name, 'asset': kind,
                        **values, 'volume': number(struct.unpack('<Q', block[56:64])[0], integer=True),
                        'amount': number(round(amount, 2))})
    return records, priced


def parse_package(content, stamp):
    ymd = stamp.replace('-', '')
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = archive.namelist()
            require(len(names) == len(set(names)) and sum(info.file_size for info in archive.infolist()) <= MAX_UNCOMPRESSED,
                    '盘后 ZIP 重复文件或解压规模异常')
            records = []
            for market in ('sh', 'sz', 'bj'):
                cod, md1 = f'{market}{ymd[2:]}.cod', f'{market}{ymd[2:]}.md1'
                if market == 'bj' and ymd < '20220506' and cod not in names and md1 not in names:
                    continue
                require(cod in names and md1 in names, '盘后包缺少预期市场或日期文件')
                rows, count = parse_market(read_member(archive, cod), read_member(archive, md1), market, stamp)
                require(count >= MIN_PRICED[market], '盘后包某市场有价记录不足，不能宣称完整包')
                records.extend(rows)
            return sorted(records, key=lambda row: row['symbol'])
    except (zipfile.BadZipFile, zlib.error, EOFError, RuntimeError) as error:
        raise ValueError('盘后包无法完整解压') from error


def read_member(archive, name):
    info = archive.getinfo(name)
    require(info.file_size <= MAX_UNCOMPRESSED, '盘后成员超出读取边界')
    chunks, size = [], 0
    with archive.open(info) as stream:
        while chunk := stream.read(65536):
            size += len(chunk)
            require(size <= info.file_size, '盘后成员实际解压长度超过声明')
            chunks.append(chunk)
    require(size == info.file_size, '盘后成员被截断')
    return b''.join(chunks)


def read_package(query, context):
    url = BASE + query.start.replace('-', '') + '.zip'
    records = parse_package(PublicRead(context.timeout_seconds).read(url), query.start)
    return paged(query, records, url, notes=(
        '一次 HTTP 下载指定日期完整包并核对各市场文件；表格仅展示当前支持的沪深北股票与沪深 ETF，不包含指数或衍生品。',
        '成交数量为股票股数／ETF 份数；沪深 ETF 已与同日腾讯手数乘 100 核对，腾讯整手取整会产生不足一手差异。',
        '价格为来源未复权原值；零成交及停牌记录不表示可交易，不将当前包反填为严格历史可见性。'))
