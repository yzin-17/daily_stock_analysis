"""央视节目元数据与同花顺编辑标签，不下载正文或生成因果结论。"""

from urllib.parse import urlparse
import re

from bs4 import BeautifulSoup
from .market_reference_projection import require
from .market_reference_public_common import PublicRead, paged, unique


def parse_cctv(content, stamp):
    soup = BeautifulSoup(content, 'html.parser')
    records = []
    seen = set()
    for item in soup.find_all('li'):
        anchor = next((a for a in item.find_all('a', href=True) if '/VIDE' in a['href']), None)
        if anchor is None:
            continue
        href = anchor['href']
        href = 'https:' + href if href.startswith('//') else href
        parsed = urlparse(href)
        require(parsed.hostname == 'tv.cctv.com' and parsed.scheme in ('http', 'https') and not parsed.username and not parsed.password,
                '央视条目跳到其他来源')
        path_date = stamp.replace('-', '/')
        require('/' + path_date + '/' in parsed.path, '央视条目返回其他节目日期')
        title = anchor.get('title') or item.get_text(' ', strip=True)
        title = re.sub(r'^\[视频\]', '', title).strip()
        require(bool(title), '央视条目缺少标题')
        url = parsed._replace(scheme='https').geturl()
        if url in seen:
            continue
        seen.add(url)
        kind = '整期节目' if '《新闻联播》' in title or '新闻联播完整版' in title else '单篇节目条目'
        records.append({'date': stamp, 'kind': kind, 'title': title, 'url': url})
    require(bool(records), '央视页面未解析出该日元数据，可能尚未发布或结构改变')
    return records


def parse_themes(payload, stamp):
    require(isinstance(payload, dict) and payload.get('errocode') == 0 and isinstance(payload.get('data'), list), '题材接口没有明确成功结果')
    require(payload.get('date') == stamp, '题材接口回显其他日期')
    require(all(isinstance(row, dict) for row in payload['data']), '题材来源行结构改变')
    records = []
    for row in payload['data']:
        code = row.get('code')
        require(isinstance(code, str) and bool(re.fullmatch(r'\d{6}', code)) and row.get('date') == stamp, '编辑题材的代码或日期不符')
        market = None
        if code.startswith('6'):
            market = 'SH'
        elif code.startswith(('0', '3')):
            market = 'SZ'
        require(market is not None and row.get('name') and row.get('reason'), '题材缺少证券或编辑标签')
        records.append({'date': stamp, 'symbol': code + '.' + market, 'name': row['name'], 'reason': row['reason']})
    unique(records, ('symbol',))
    return records


def read_editorial(query, context):
    read = PublicRead(context.timeout_seconds)
    if query.dataset == 'cctv-news':
        url = 'https://tv.cctv.com/lm/xwlb/day/' + query.start.replace('-', '') + '.shtml'
        records = parse_cctv(read.read(url), query.start)
        notes = ('仅节目元数据和原始链接，整期节目与单篇条目分开；节目日期不冒充每篇新闻的精确发布时间。未下载文字稿或视频。',)
    else:
        url = f'https://zx.10jqka.com.cn/event/api/getharden/date/{query.start}/orderby/date/orderway/desc/charset/GBK/'
        records = parse_themes(read.json(url), query.start)
        notes = ('题材归因为同花顺编辑标签，未由本系统独立验证，不等于真实涨跌原因、题材生命周期或策略评分。',
                 '只展示代码、名称、日期和编辑标签；不复制参考实现中未经核定的成交额单位。')
    return paged(query, records, url, notes=notes)
