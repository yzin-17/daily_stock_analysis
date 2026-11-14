"""新闻维护入口的离线边界测试，不执行 Docker、真实源或业务写入。"""
import base64
from datetime import datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('news_admission_worker', Path(__file__).with_name('news-admission.py'))
worker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(worker)
NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)
CONTENT = b'controlled-feed-only'
KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'NEWS_SEARCH'}
TARGET = {'providerId': 'akshare', 'upstreamSource': 'eastmoney'}


def payload():
    return {'mode': 'apply', 'project': 'thesis-ledger-dev', 'providerId': 'akshare', 'symbol': '600519.SH',
            'start': '2026-10-19', 'end': '2026-10-29', 'validHours': 168, 'recordedBy': 'offline-test',
            'observedAt': NOW.isoformat(), 'feedSha256': sha256(CONTENT).hexdigest(),
            'feedBase64': base64.b64encode(CONTENT).decode(), 'license': 'public-metadata-research'}


def parse(_):
    return [SimpleNamespace(title='受控标题', url='https://source.test/news/item',
                            published_at='2026-10-21T12:00:00+00:00', publisher='受控媒体')]


class NewsAdmissionContracts(unittest.TestCase):
    def test_unreviewed_provider_is_rejected_before_parsing_or_database_access(self):
        request = {**payload(), 'providerId': 'unreviewed'}
        with self.assertRaisesRegex(AssertionError, '未审阅'):
            worker.validate_evidence(request, NOW, lambda _: self.fail('不得解析未知来源'))
        with patch.object(worker, 'admission_only_store', side_effect=AssertionError('不得访问数据库')):
            with self.assertRaisesRegex(AssertionError, '未审阅'):
                worker.main(request)

    def test_rolling_flag_is_optional_for_legacy_renewal_and_requires_explicit_true(self):
        now = datetime(2026, 10, 30, 14, tzinfo=timezone.utc)
        request = {**payload(), 'providerId': 'hithink', 'symbol': '600519.SH',
                   'license': 'account-news-metadata', 'start': '2026-10-23', 'end': '2026-10-30',
                   'observedAt': now.isoformat(), 'validHours': 1, 'renewExisting': True,
                   'expectedRecordVersion': 3, 'expectedRecordSha256': 'a' * 64}
        profile = ({'kind': 'data', 'market': 'CN', 'assetType': 'STOCK', 'capability': 'NEWS_SEARCH'},
                   {'providerId': 'hithink', 'upstreamSource': 'hithink-news-stock-timeline'},
                   '600519.SH', 'account-news-metadata', 'https://source.test/metadata')
        rows = [SimpleNamespace(title='公开标题', url='https://source.test/item',
                                published_at='2026-10-29T12:00:00+00:00', publisher='公开媒体')]
        with patch.object(worker, 'news_profile', return_value=profile):
            for extra in [{}, {'rollRecentWindow': True}]:
                value = worker.validate_evidence({**request, **extra}, now, lambda _: rows)
                self.assertEqual(value['symbol'], '600519.SH')
            with self.assertRaises(AssertionError):
                worker.validate_evidence({**request, 'rollRecentWindow': False}, now, lambda _: rows)
            plain = {k: v for k, v in request.items() if k not in worker.RENEWAL_FIELDS}
            with self.assertRaises(AssertionError):
                worker.validate_evidence({**plain, 'rollRecentWindow': True}, now, lambda _: rows)

    def test_flash_metadata_has_market_scope_and_rejects_body_or_foreign_endpoint(self):
        from data_provider.market_flash_news import parse_flash_page
        for provider, row, data_key, code in [
            ('cls', {'id': 123, 'title': '公开标题', 'brief': None, 'ctime': 1792672493,
                     'shareurl': 'https://api3.cls.cn/share/article/123'}, 'roll_data', {}),
            ('wallstreetcn', {'id': 123, 'title': '公开标题', 'content_text': None, 'display_time': 1792672493,
                             'uri': 'https://wallstreetcn.com/livenews/123'}, 'items', {'code': 20000}),
        ]:
            body = json.dumps({**code, 'data': {data_key: [row]}}).encode()
            metadata = parse_flash_page(body, provider, max_results=5).metadata
            self.assertEqual(worker.parse_flash_metadata(json.dumps(metadata).encode(), provider)[0].title, '公开标题')
            key, target, symbol, *_ = worker.news_profile({'providerId': provider})
            self.assertEqual((key['capability'], symbol), ('NEWS_FEED', 'CN.STOCK'))
            self.assertEqual(worker.current_credential_revision('/not-read.db', target), 'not-required')
            for field, value in [('scope', '600519.SH'), ('upstreamCommit', 'unreviewed'), ('endpoint', 'https://other.test')]:
                with self.assertRaises(AssertionError):
                    worker.parse_flash_metadata(json.dumps({**metadata, field: value}).encode(), provider)
            metadata['payload']['data'][data_key][0]['body'] = '不得登记的正文'
            with self.assertRaises(AssertionError):
                worker.parse_flash_metadata(json.dumps(metadata).encode(), provider)

    def test_yahoo_search_proof_is_metadata_only_and_keeps_fixed_scope(self):
        from data_provider.yahoo_news import SDK_VERSION, SEARCH_URL
        metadata = {'symbol': 'NVDA.US', 'sdkVersion': SDK_VERSION, 'endpoint': SEARCH_URL,
                    'responseSha256': 'a' * 64, 'payload': {
                        'quotes': [{'symbol': 'NVDA', 'quoteType': 'EQUITY'}], 'news': [{
                            'uuid': 'controlled-yahoo', 'title': '公开标题',
                            'link': 'https://finance.yahoo.com/news/story.html', 'publisher': '公开媒体',
                            'providerPublishTime': 1792645918, 'relatedTickers': ['NVDA']}]}}
        records = worker.parse_yahoo_metadata(json.dumps(metadata).encode())
        self.assertEqual(records[0].title, '公开标题')
        self.assertIsNone(records[0].excerpt)
        request = {**payload(), 'providerId': 'yfinance', 'symbol': 'NVDA.US', 'license': 'public-metadata-research'}
        key, target, symbol, *_ = worker.news_profile(request)
        self.assertEqual((key['market'], target['upstreamSource'], symbol), ('US', 'yahoo-search-news', 'NVDA.US'))
        self.assertEqual(worker.current_credential_revision('/missing/not-read.db', target), 'not-required')
        for field, value in [('symbol', 'AAPL.US'), ('sdkVersion', 'unreviewed'),
                             ('endpoint', 'https://other.test'), ('responseSha256', '')]:
            with self.assertRaises(AssertionError):
                worker.parse_yahoo_metadata(json.dumps({**metadata, field: value}).encode())
        metadata['payload']['news'][0]['summary'] = '不保存正文'
        with self.assertRaises(AssertionError):
            worker.parse_yahoo_metadata(json.dumps(metadata).encode())

    def test_hithink_metadata_only_proof_and_endpoint(self):
        metadata = {'symbol': '600519.SH',
                    'endpoint': 'https://fuyao.aicubes.cn/api/news/stock/timeline-list',
                    'responseSha256': 'd' * 64,
                    'payload': {'code': 0, 'data': {'has_more': True, 'offset': 'opaque-next', 'items': [{
                        'id': 'controlled-id', 'title': '公开标题', 'source': '公开媒体',
                        'publish_time_ms': 1792645918000,
                        'jump_url': 'https://news.10jqka.com.cn/20261022/c680969463.shtml'}]}}}
        value = worker.parse_hithink_metadata(json.dumps(metadata).encode())
        self.assertEqual(value[0].title, '公开标题')
        self.assertIsNone(value[0].excerpt)
        for field, changed in [('symbol', '000001.SZ'), ('endpoint', 'https://other.test'), ('responseSha256', '')]:
            with self.assertRaises(AssertionError):
                worker.parse_hithink_metadata(json.dumps({**metadata, field: changed}).encode())
        metadata['payload']['data']['items'][0]['summary'] = '不保存正文'
        with self.assertRaises(AssertionError):
            worker.parse_hithink_metadata(json.dumps(metadata).encode())

    def test_credential_guard_is_checked_inside_write_transaction(self):
        request = {**payload(), 'providerId': 'hithink', 'symbol': '600519.SH', 'license': 'account-news-metadata'}
        key, target, *_ = worker.news_profile(request)
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'controlled.db'
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE thesis_ledger_route_admission_v3 '
                                   '(consumer TEXT, provider_id TEXT, upstream_source TEXT, route_key_json TEXT)')
            state = {'same': True}
            connection = worker.insert_only_connection(db, key, target, lambda: state['same'])
            state['same'] = False
            try:
                with self.assertRaisesRegex(ValueError, '凭据.*已变化'):
                    with connection:
                        connection.execute('BEGIN IMMEDIATE')
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.execute('SELECT count(*) FROM thesis_ledger_route_admission_v3').fetchone()[0], 0)
            finally:
                connection.close()

    def test_metadata_proof_rejects_body_version_and_identity_changes(self):
        metadata = {'symbol': '600519.SH', 'sdkVersion': 'reviewed', 'sdkFunctionSha256': 'a' * 64,
                    'rows': [{'关键词': '600519', '新闻标题': '公开标题', '发布时间': '2026-10-21 16:00:00',
                              '文章来源': '公开媒体', '新闻链接': 'https://finance.eastmoney.com/a/202610213902666262.html'}]}
        module = SimpleNamespace(SDK_VERSIONS={'reviewed'}, SDK_FUNCTION_SHA256='a' * 64,
                                 project_news=lambda frame, symbol: (tuple(frame['关键词']), symbol))
        with patch.dict(sys.modules, {'data_provider.akshare_news': module}):
            self.assertEqual(worker.parse_akshare_metadata(json.dumps(metadata).encode()), (('600519',), '600519'))
            for field, value in [('symbol', '000001.SZ'), ('sdkVersion', 'unknown'), ('sdkFunctionSha256', 'b' * 64)]:
                changed = {**metadata, field: value}
                with self.assertRaises(AssertionError):
                    worker.parse_akshare_metadata(json.dumps(changed).encode())
            with self.assertRaises(AssertionError):
                worker.parse_akshare_metadata(json.dumps({**metadata, 'rows': [{**metadata['rows'][0], '新闻内容': '正文'}]}).encode())

    def test_existing_source_profile_and_concurrent_rejection(self):
        request = {**payload(), 'providerId': 'akshare', 'symbol': '600519.SH', 'license': 'public-metadata-research'}
        evidence = worker.validate_evidence(request, NOW, parse)
        self.assertEqual(evidence['source'], 'https://so.eastmoney.com/news/s?keyword=600519')
        self.assertEqual(evidence['symbol'], '600519.SH')
        key, target, *_ = worker.news_profile(request)
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'controlled.db'
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE thesis_ledger_route_admission_v3 '
                                   '(consumer TEXT, provider_id TEXT, upstream_source TEXT, route_key_json TEXT)')
                connection.execute('INSERT INTO thesis_ledger_route_admission_v3 VALUES (?,?,?,?)',
                                   ('thesis-ledger', 'akshare', 'eastmoney', json.dumps(key)))
                with self.assertRaisesRegex(ValueError, '已有新闻准入'):
                    worker.assert_first_admission(connection, key, target)
            connection = worker.insert_only_connection(db, key, target)
            try:
                with self.assertRaisesRegex(ValueError, '已有新闻准入'):
                    with connection:
                        connection.execute('BEGIN IMMEDIATE')
                self.assertFalse(connection.in_transaction)
            finally:
                connection.close()

    def test_metadata_only_evidence(self):
        value = worker.validate_evidence(payload(), NOW, parse)
        self.assertEqual(value['symbol'], '600519.SH')
        self.assertEqual(set(value['items'][0]), {'title', 'url', 'publishedAt', 'publisher'})

    def test_explicit_reviewed_extension_allows_seven_days_without_new_expiry(self):
        request = {**payload(), 'providerId': 'akshare', 'symbol': '600519.SH',
                   'license': 'public-metadata-research', 'start': '2026-10-15', 'validHours': None,
                   'extendExisting': True, 'expectedRecordVersion': 1, 'expectedRecordSha256': 'a' * 64}
        self.assertEqual(worker.validate_evidence(request, NOW, parse)['scopeDateFrom'], '2026-10-15')
        for fields in [{'start': '2026-10-14'}, {'validHours': 168}, {'expectedRecordVersion': 0},
                       {'expectedRecordSha256': ''}, {'providerId': 'yfinance', 'symbol': 'NVDA.US'}]:
            with self.subTest(fields=fields), self.assertRaises(AssertionError):
                worker.validate_evidence({**request, **fields}, NOW, parse)

    def test_scope_expiry_provenance_and_changed_bytes_are_rejected(self):
        for key, value in [('project', 'production'), ('symbol', 'MSFT.US'), ('start', '2026-10-18'),
                           ('end', '2026-10-30'), ('license', 'commercial'), ('validHours', True),
                           ('validHours', 169), ('observedAt', '2026-10-20T00:00:00Z'),
                           ('observedAt', '2026-10-23T00:00:00Z'), ('feedSha256', '0' * 64),
                           ('recordedBy', ''), ('unknown', 'value')]:
            request = payload(); request[key] = value
            with self.subTest(key=key), self.assertRaises((AssertionError, ValueError)):
                worker.validate_evidence(request, NOW, parse)
        with self.assertRaises(AssertionError):
            worker.validate_evidence(payload(), NOW, lambda _: [])

    def test_ordinary_store_constructor_is_never_called(self):
        class Existing:
            def __init__(self):
                raise AssertionError('禁止结构初始化或凭据轮换')
        with patch.dict(sys.modules, {'src.services.thesis_ledger_control': SimpleNamespace(ThesisLedgerControlStore=Existing)}):
            store = worker.admission_only_store('/app/data/stock_analysis.db', KEY, TARGET)
        self.assertEqual(store.database_path, '/app/data/stock_analysis.db')

    def test_missing_db_not_created_and_concurrent_record_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / 'synthetic.db'
            with self.assertRaises(sqlite3.OperationalError):
                worker.insert_only_connection(db, KEY, TARGET)
            self.assertFalse(db.exists())
            with sqlite3.connect(db) as connection:
                connection.execute('CREATE TABLE thesis_ledger_route_admission_v3 '
                                   '(consumer TEXT, provider_id TEXT, upstream_source TEXT, route_key_json TEXT)')
                worker.assert_first_admission(connection, KEY, TARGET)
                connection.execute('INSERT INTO thesis_ledger_route_admission_v3 VALUES (?,?,?,?)',
                                   ('thesis-ledger', 'akshare', 'eastmoney', json.dumps(KEY)))
            connection = worker.insert_only_connection(db, KEY, TARGET)
            try:
                with self.assertRaisesRegex(ValueError, '已有新闻准入'):
                    with connection:
                        connection.execute('BEGIN IMMEDIATE')
                self.assertFalse(connection.in_transaction)
                self.assertEqual(connection.execute('SELECT count(*) FROM thesis_ledger_route_admission_v3').fetchone()[0], 1)
            finally:
                connection.close()


if __name__ == '__main__':
    unittest.main()
