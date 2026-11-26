"""官方完整公告目录与事件日期重建；只认证已审阅的无事件窗口。"""

from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

METHOD = 'sse-disclosure-event-window-v1'
ACTION = re.compile('分红|收益分配|折算|拆分|份额转换')
ROOT = Path(__file__).resolve().parent / 'market_reference' / 'historical_event_coverage'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def day(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, '日期无效')
    return value


def instant(value):
    require(isinstance(value, str), '时钟无效')
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    require(result.tzinfo is not None, '时钟缺少时区')
    return result


def read_bytes(path, maximum):
    require(not path.is_symlink() and not path.parent.is_symlink(), '原文路径不得为软链接')
    require(path.is_file() and path.stat().st_size <= maximum, '原文缺失或过大')
    with path.open('rb') as stream:
        raw = stream.read(maximum + 1)
    require(len(raw) <= maximum, '原文超过预算')
    return raw


def inventory(root):
    return sha256(read_bytes(Path(root) / 'proof.json', 512 * 1024)).hexdigest()


def label_date(text, label):
    # 只读取表格标签的直接日期，不跨句抓取正文汇款日期。
    values = re.findall(label + r'[:：]*(\d{4})年(\d{1,2})月(\d{1,2})日', text)
    require(values and len({tuple(map(int, item)) for item in values}) == 1, '公告日期缺失或歧义')
    return date(*map(int, values[0])).isoformat()


@lru_cache(maxsize=32)
def publication_dates(raw, symbol):
    from pypdf import PdfReader
    require(len(raw) <= 512 * 1024, '公告原文超过预算')
    reader = PdfReader(BytesIO(raw))
    require(1 <= len(reader.pages) <= 10, '公告页数不符')
    text = re.sub(r'\s+', '', ''.join(page.extract_text() or '' for page in reader.pages))
    require(len(text) <= 300000 and symbol.split('.')[0] in text, '公告证券或文本不符')
    return (label_date(text, '权益登记日'), label_date(text, '除息日'), label_date(text, '(?:现金)?红利发放日'))


def read_catalog(item, read_original, scope_end):
    require(set(item) == {'symbol', 'listedOn', 'reference', 'sha256', 'observedAt'}, '目录证明字段无效')
    symbol, listed = item['symbol'], day(item['listedOn'])
    require(re.fullmatch(r'\d{6}\.SH', symbol), '只支持上交所 ETF')
    parsed = urlsplit(item['reference'])
    require(parsed.scheme == 'https' and parsed.hostname == 'query.sse.com.cn' and not parsed.username
        and not parsed.password and parsed.port in (None, 443) and parsed.path == '/commonQuery.do'
        and not parsed.fragment, '目录必须是官方精确查询')
    params = parse_qs(parsed.query, keep_blank_values=True)
    end = params.get('END_DATE', [''])[0]
    day(end)
    expected = {'isPagination': ['true'], 'pageHelp.pageSize': ['1000'], 'pageHelp.pageNo': ['1'],
        'pageHelp.beginPage': ['1'], 'pageHelp.cacheSize': ['1'], 'pageHelp.endPage': ['1'],
        'type': ['inParams'], 'sqlId': ['COMMON_PL_JJXX_JJGG_NEW_L'], 'TITLE': [''],
        'SECURITY_CODE': [symbol.split('.')[0]], 'BULLETIN_TYPE': [''], 'OTHER_TYPE': [''],
        'START_DATE': [listed], 'END_DATE': [end], 'DATE_DESC': ['1'], 'DATE_ASC': [''],
        'CODE_DESC': [''], 'CODE_ASC': ['']}
    require(params == expected, '目录查询含过滤、错误证券或不完整起点')
    observed = instant(item['observedAt']).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    require(scope_end <= end <= observed and listed <= end, '目录未覆盖请求截止或采集时钟倒置')
    raw = read_original(item['sha256'])
    text = raw.decode('utf-8').strip()
    body = json.loads(text[1:-1] if text.startswith('(') and text.endswith(')') else text)
    require(isinstance(body, dict) and not body.get('actionErrors') and not body.get('actionMessages')
        and not body.get('fieldErrors') and body.get('success') not in ('false', False), '官方目录业务报错')
    rows, page = body.get('result'), body.get('pageHelp')
    require(isinstance(rows, list) and 1 <= len(rows) <= 1000 and isinstance(page, dict), '目录条目不完整')
    require(all(type(page.get(key)) is int for key in ['pageNo', 'pageCount', 'total', 'pageSize'])
        and page['pageNo'] == page['pageCount'] == 1 and page['total'] == len(rows)
        and page['pageSize'] == 1000 and page.get('data') == rows, '目录分页或总数不完整')
    require(len({row['URL'] for row in rows}) == len(rows), '目录条目重复')
    for row in rows:
        require(row['SECURITY_CODE'] == symbol.split('.')[0] and listed <= day(row['SSEDATE']) <= end,
                '目录证券或日期不符')
        require(isinstance(row['TITLE'], str) and row['TITLE'] and isinstance(row['ORG_BULLETIN_TYPE_DESC'], str)
            and row['URL'].startswith('/disclosure/fund/announcement/'), '目录标题或原文路径缺失')
    return rows


def validate_material(pack, read_original):
    require(set(pack) == {'version', 'method', 'scopeSymbols', 'scopeDateFrom', 'scopeDateTo', 'indexes', 'publications'},
            '事件归档字段无效')
    require(type(pack['version']) is int and pack['version'] == 1 and pack['method'] == METHOD, '事件归档版本无效')
    start, end = day(pack['scopeDateFrom']), day(pack['scopeDateTo'])
    symbols = pack['scopeSymbols']
    require(start <= end and isinstance(symbols, list) and 1 <= len(symbols) <= 20
        and symbols == sorted(set(symbols)) and all(re.fullmatch(r'\d{6}\.SH', s) for s in symbols), '归档范围无效')
    require(isinstance(pack['indexes'], list) and sorted(x['symbol'] for x in pack['indexes']) == symbols,
            '目录未完整覆盖证券')
    candidates = {}
    for item in pack['indexes']:
        require(item['listedOn'] <= start, '目标证券尚未上市')
        rows = read_catalog(item, read_original, end)
        for row in rows:
            if ACTION.search(row['TITLE']) or ACTION.search(row['ORG_BULLETIN_TYPE_DESC']):
                require(not re.search('折算|拆分|份额转换', row['TITLE'] + row['ORG_BULLETIN_TYPE_DESC']),
                        '折算公告尚未取得明确生效日期，不能签发无事件覆盖')
                key = 'https://www.sse.com.cn' + row['URL']
                require(key not in candidates, '相关公告重复')
                candidates[key] = (item['symbol'], row['SSEDATE'])
    publications = pack['publications']
    require(isinstance(publications, list) and len(publications) <= 24
        and len({item['reference'] for item in publications}) == len(publications)
        and {item['reference'] for item in publications} == set(candidates), '相关公告缺失或多余')
    for item in publications:
        require(set(item) == {'symbol', 'publishedOn', 'reference', 'sha256', 'observedAt',
            'recordDate', 'effectiveDate', 'paymentDate'}, '事件公告证明字段无效')
        require(candidates[item['reference']] == (item['symbol'], item['publishedOn']), '公告与目录不符')
        require(re.fullmatch(r'https://www\.sse\.com\.cn/disclosure/fund/announcement/c/(?:new/)?\d{4}-\d{2}-\d{2}/'
            + item['symbol'].split('.')[0] + r'_[^/]+\.pdf', item['reference']), '公告原文路径不符')
        require(instant(item['observedAt']).date().isoformat() >= item['publishedOn'], '公告采集时钟倒置')
        actual = publication_dates(read_original(item['sha256']), item['symbol'])
        expected = tuple(day(item[key]) for key in ['recordDate', 'effectiveDate', 'paymentDate'])
        require(actual == expected and day(item['publishedOn']) <= expected[0] <= expected[1] <= expected[2], '公告日期不符')
        require(all(not start <= value <= end for value in expected), '请求窗口存在事件，不能签发无事件覆盖')
    return pack


class EventCoverageArchive:
    def __init__(self, root=ROOT, revision=None):
        self.root = Path(root)
        require(not self.root.is_symlink(), '归档根路径不得为软链接')
        self.revision = revision if revision is not None else inventory(self.root)

    def original(self, digest):
        require(isinstance(digest, str) and re.fullmatch(r'[a-f0-9]{64}', digest), '原文摘要无效')
        raw = read_bytes(self.root / 'originals' / digest, 1024 * 1024)
        require(sha256(raw).hexdigest() == digest, '原文字节摘要不符')
        return raw

    def material(self):
        raw = read_bytes(self.root / 'proof.json', 512 * 1024)
        require(sha256(raw).hexdigest() == self.revision, '事件归档修订变化，必须重新加载及准入')
        pack = validate_material(json.loads(raw), self.original)
        require(inventory(self.root) == self.revision, '读取期间事件归档变化')
        return pack

    def scope(self, symbol, start, end, as_of):
        pack = self.material()
        require(symbol in pack['scopeSymbols'] and pack['scopeDateFrom'] <= day(start) <= day(end) <= pack['scopeDateTo'],
                '证券或日期超出事件归档范围')
        require(end <= instant(as_of).astimezone(timezone(timedelta(hours=8))).date().isoformat(), '拒绝未来事件窗口')
        return pack
