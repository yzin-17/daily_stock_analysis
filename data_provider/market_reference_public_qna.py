"""沪深公开公司问答；参考 a-stock-data §10，问题与公司回复分开。"""

from datetime import datetime, timedelta, timezone
import re
from urllib.parse import urlencode

from bs4 import BeautifulSoup
from .market_reference_projection import require
from .market_reference_public_common import PublicRead, source_result, unique

SSE = 'https://sns.sseinfo.com'
CNINFO = 'https://irm.cninfo.com.cn/newircs/'
CN = timezone(timedelta(hours=8))


def parse_sse_qna(content):
    soup = BeautifulSoup(content, 'html.parser')
    records = []
    for item in soup.select('div.m_feed_item'):
        identifier = re.fullmatch(r'item-(\d+)', item.get('id', ''))
        ask = item.select_one('div.m_qa_detail')
        require(identifier and ask, '沪市问答缺少来源编号或提问块')
        text = ask.select_one('div.m_feed_txt')
        clock = ask.select_one('div.m_feed_from > span')
        company = text.find('a') if text else None
        matched = re.fullmatch(r':?(.+?)[（(](\d{6})[）)]', company.get_text(strip=True)) if company else None
        require(matched and clock, '沪市问答的公司身份或时间结构改变')
        full_text = text.get_text(' ', strip=True)
        company_text = company.get_text(strip=True)
        require(full_text.startswith(company_text), '沪市问答公司位置改变')
        question = full_text[len(company_text):].strip()
        require(bool(question) and bool(clock.get_text(strip=True)), '沪市问答提问或时间为空')
        reply = item.select_one('div.m_feed_detail.m_qa')
        answer = answered = None
        if reply:
            body = reply.select_one('div.m_feed_txt')
            when = reply.select_one('div.m_feed_from > span')
            require(body and when, '沪市问答回复块缺正文或时间')
            answer, answered = body.get_text(' ', strip=True), when.get_text(strip=True)
            require(bool(answer) and bool(answered), '沪市问答回复字段为空')
        records.append({'sourceId': identifier.group(1), 'symbol': matched.group(2) + '.SH',
                        'name': matched.group(1), 'question': question, 'askedTime': clock.get_text(strip=True),
                        'answer': answer, 'answeredTime': answered})
    if not records:
        require(bool(re.search(r'暂无|暂时没有', soup.get_text())), '沪市页面没有问答或明确无记录提示')
    unique(records, ('sourceId',))
    return records


def epoch_text(value):
    if value is None:
        return None
    require(type(value) is int and value > 0, '深市问答来源时间戳无效')
    return datetime.fromtimestamp(value / 1000, CN).isoformat(timespec='seconds')


def parse_szse_qna(payload, query):
    require(isinstance(payload, dict) and isinstance(payload.get('rows'), list), '深市问答封套改变')
    rows = payload['rows']
    require(all(isinstance(row, dict) for row in rows), '深市问答行结构改变')
    require(type(payload.get('total')) is int and payload['total'] >= 0, '深市问答缺少总数')
    require(payload.get('pageNo') == query.page and payload.get('pageSize') == query.pageSize, '深市问答分页回显不符')
    records = []
    for row in rows:
        require(row.get('stockCode') == query.symbol[:6] and row.get('companyShortName'), '深市问答返回其他证券')
        require(isinstance(row.get('indexId'), str) and bool(re.fullmatch(r'\d+', row['indexId'])), '深市问答编号无效')
        asked = epoch_text(row.get('pubDate'))
        require(row.get('mainContent') and asked, '深市问答提问或时间缺失')
        answer = row.get('attachedContent') or None
        answered = epoch_text(row.get('attachedPubDate')) if answer else None
        records.append({'sourceId': row['indexId'], 'symbol': query.symbol, 'name': row['companyShortName'],
                        'question': row['mainContent'], 'askedTime': asked,
                        'answer': answer, 'answeredTime': answered})
    unique(records, ('sourceId',))
    return records


def read_qna(query, context):
    read = PublicRead(context.timeout_seconds)
    if query.dataset == 'sse-company-qna':
        params = {'type': '11', 'pageSize': str(query.pageSize), 'lastid': '-1', 'show': '1', 'page': str(query.page)}
        url = SSE + '/ajax/feeds.do'
        records = parse_sse_qna(read.read(url, params, {'Referer': SSE + '/'}))
        return source_result(query, records, url + '?' + urlencode(params),
                             has_more=len(records) == query.pageSize,
                             notes=('沪市全市场近期问答，不自动关联用户持仓；不代表完整历史。下一页可能为空。',
                                    '来源的绝对／相对时间文字按原样保留，请结合抓取时间阅读；没有虚构秒级发布时间。投资者提问不等于公司确认事实。'))
    located = read.json(CNINFO + 'index/queryKeyboardInfo', method='POST', data={'keyWord': query.symbol[:6]})
    require(isinstance(located, dict) and isinstance(located.get('data'), list), '深市机构定位结构改变')
    require(all(isinstance(row, dict) for row in located['data']), '深市机构定位行结构改变')
    matches = [row for row in located['data'] if row.get('stockCode') == query.symbol[:6]]
    require(len(matches) == 1 and bool(re.fullmatch(r'[a-zA-Z0-9]+', matches[0].get('secid', ''))), '深市机构定位不唯一或身份缺失')
    params = {'_t': '1', 'stockcode': query.symbol[:6], 'orgId': matches[0]['secid'],
              'pageSize': str(query.pageSize), 'pageNum': str(query.page), 'keyWord': '', 'startDay': '', 'endDay': ''}
    payload = read.json(CNINFO + 'company/question', params, method='POST')
    records = parse_szse_qna(payload, query)
    return source_result(query, records, 'https://irm.cninfo.com.cn/', total=payload['total'],
                         has_more=query.page * query.pageSize < payload['total'],
                         notes=('深市公司近期问答，证券定位与返回行均核对；未回复保持空值，提问时间与回复时间分别记录。',
                                '时间戳转为北京时间；未提供回复时间时不使用更新时间代替，来源主页可按公司及问答编号反查。'))
