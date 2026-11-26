"""离线核验真实采集回执、原文字节与已审阅的页面提取清单。"""

from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
from io import BytesIO
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from zipfile import ZipFile

ZONE = timezone(timedelta(hours=8))
HOSTS = {'www.sse.com.cn', 'query.sse.com.cn', 'yunhq.sse.com.cn', 'etf.sse.com.cn',
         'www.chinaclear.cn', 'm.chinaclear.cn', 'www.chinaamc.com', 'shanghai.chinatax.gov.cn'}


def compact(value):
    return re.sub(r'\s+', '', unescape(value))


def day_end(day):
    return datetime.combine(date.fromisoformat(day), time(23, 59, 59, 999000), ZONE).isoformat()


class OfficialSources:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.raw, self.receipts = {}, {}

    def read(self, group, ident, expected):
        if not re.fullmatch(r'[a-zA-Z0-9-]+', group + ident):
            raise ValueError('采集材料路径标识无效')
        path = self.root / group / 'receipt.json'
        if path.is_symlink():
            raise ValueError('采集回执不允许符号链接')
        matches = [x for x in json.loads(path.read_text())['results'] if x['id'] == ident]
        if len(matches) != 1:
            raise ValueError('采集回执缺失或身份重复')
        entry = matches[0]
        for reference in [entry['url'], entry.get('finalUrl', entry['url'])]:
            url = urlsplit(reference)
            if (url.scheme != 'https' or url.hostname not in HOSTS or url.username or url.password
                    or url.port not in ((None, 443, 32042) if url.hostname == 'yunhq.sse.com.cn' else (None, 443))):
                raise ValueError('真实材料必须来自已审阅的官方端点')
        raw_path = self.root / group / entry['file']
        if raw_path.parent != self.root / group or raw_path.is_symlink() or raw_path.parent.is_symlink():
            raise ValueError('原文文件路径无效')
        if raw_path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('原文超过离线处理预算')
        raw = raw_path.read_bytes()
        if (entry['status'] != 200 or entry['bytes'] != len(raw)
                or entry['sha256'] != expected or sha256(raw).hexdigest() != expected):
            raise ValueError('真实材料回执、字节或固定摘要不符')
        self.raw[expected] = raw
        self.receipts[ident] = entry
        return raw, entry

    def document(self, ident, spec):
        raw, entry = self.read(spec['group'], spec['captureId'], spec['sha256'])
        if spec['format'] == 'pdf':
            from pypdf import PdfReader
            reader = PdfReader(BytesIO(raw))
            text = '\n'.join(reader.pages[n - 1].extract_text() or '' for n in spec['pages'])
        elif spec['format'] == 'docx':
            with ZipFile(BytesIO(raw)) as archive:
                member = archive.getinfo('word/document.xml')
                if member.file_size > 16 * 1024 * 1024:
                    raise ValueError('文档解压超过预算')
                text = archive.read(member).decode()
            text = re.sub('<[^>]*>', '', text)
        elif spec['format'] == 'html':
            text = re.sub('<[^>]*>', '', raw.decode())
        else:
            raise ValueError('未经审阅的文档格式')
        normalized = compact(text)
        if not all(compact(fragment) in normalized for fragment in spec['requires']):
            raise ValueError(f'真实原文提取断言不符：{ident}')
        # 日末是历史可见性保守上界，不冒充官方精确发布时间；当前字节若较晚修改则采用更晚上界。
        latest = date.fromisoformat(spec['publishedOn'])
        if entry.get('lastModified'):
            latest = max(latest, parsedate_to_datetime(entry['lastModified']).astimezone(ZONE).date())
        evidence = {'id': ident, 'kind': 'rule', 'sha256': spec['sha256'], 'reference': entry['url'],
                    'availableAt': day_end(latest.isoformat()), 'observedAt': entry['observedAt']}
        return evidence, normalized
