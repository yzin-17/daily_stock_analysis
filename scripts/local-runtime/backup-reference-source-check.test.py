"""备用源采证的代码／日期边界和无凭据、无跳转、独占证据验证。"""

import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from backup_reference_source_cases import PROFILES, request_spec
from backup_reference_source_links import follow_request

SPEC = importlib.util.spec_from_file_location('public_source_check', Path(__file__).with_name('public-reference-source-check.py'))
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class BackupSourceCheck(unittest.TestCase):
    def parent(self, directory, profile, body, *, code='600519', status=200, location=None):
        path = Path(directory, 'parent.json')
        spec = request_spec(profile, '2026-11-03', code)
        path.with_suffix('.body').write_bytes(body)
        path.write_text(json.dumps({'profile': profile, 'date': '2026-11-03', 'code': code,
            'request': spec, 'response': {'httpStatus': status, 'sha256': sha256(body).hexdigest(),
                'truncated': False, 'url': spec['url'], 'location': location}}))
        return str(path)

    def test_arbitrary_endpoints_invalid_dates_and_codes_rejected(self):
        for args in [('https://example.com', '2026-11-03', '600519'),
                     ('backup-ths-debt', '2026-02-30', '600519'),
                     ('backup-ths-debt', '2026-11-03', '../600519'),
                     ('backup-ths-debt', '2026-11-03', '920002')]:
            with self.assertRaises(ValueError):
                request_spec(*args)

    def test_exchange_identity_is_checked(self):
        for profile, code in [('backup-margin-sh', '300750'), ('backup-dragon-sz', '600519')]:
            with self.assertRaises(ValueError):
                request_spec(profile, '2026-11-03', code)

    def test_three_financial_statements_have_distinct_explicit_endpoints(self):
        for suffix in ['debt', 'benefit', 'cash']:
            spec = request_spec('backup-ths-' + suffix, '2026-11-03', '600519')
            self.assertEqual(spec['url'], f'https://basic.10jqka.com.cn/api/stock/finance/600519_{suffix}.json')
            self.assertEqual(spec['params'], {})

    def test_margin_date_and_completeness_request(self):
        sh = request_spec('backup-margin-sh', '2026-11-03', '600519')
        self.assertEqual(sh['params']['detailsDate'], '20261103')
        self.assertEqual(sh['params']['pageHelp.pageSize'], '5000')
        sz = request_spec('backup-margin-sz', '2026-11-03', '300750')
        self.assertEqual(sz['params'], {'SHOWTYPE': 'xlsx', 'CATALOGID': '1837_xxpl', 'TABKEY': 'tab2', 'txtDate': '2026-11-03'})

    def test_dragon_tiger_market_and_date_remain_distinct(self):
        sh = request_spec('backup-dragon-sh', '2026-11-03', '600519')
        sz = request_spec('backup-dragon-sz', '2026-11-03', '300750')
        self.assertIn('showTradePublicFile.do', sh['url'])
        self.assertEqual(sh['params']['dateTx'], '2026-11-03')
        self.assertEqual(sz['params']['txtStart'], sz['params']['txtEnd'])
        self.assertEqual(sz['params']['CATALOGID'], '1842_xxpl')

    def test_research_is_single_symbol_single_page(self):
        spec = request_spec('backup-sina-reports', '2026-11-03', '600519')
        self.assertEqual(spec['params'], {'symbol': '600519', 't1': 'all', 'p': '1'})
        self.assertEqual(len(PROFILES), 8)

    def test_transport_keeps_tls_and_suppresses_implicit_credentials_and_redirects(self):
        response = MagicMock()
        response.status_code = 200
        response.headers = {'Content-Type': 'application/json'}
        response.url = 'https://basic.10jqka.com.cn/api/stock/finance/600519_debt.json'
        response.iter_content.return_value = [b'{"data": []}']
        response.__enter__.return_value = response
        session = MagicMock()
        session.__enter__.return_value = session
        session.request.return_value = response
        with tempfile.TemporaryDirectory() as directory, patch('requests.Session', return_value=session):
            CLI.execute('backup-ths-debt', '2026-11-03', '600519', directory)
            self.assertTrue(callable(session.auth))
            self.assertIs(session.request.call_args.kwargs['allow_redirects'], False)
            self.assertIs(session.request.call_args.kwargs.get('verify', True), True)
            receipt = json.loads(Path(directory, 'backup-ths-debt-1.json').read_text())
            self.assertEqual(receipt['configurationWrites'], 0)
            self.assertEqual(receipt['modelCalls'], 0)
            self.assertEqual(receipt['newCostUSD'], 0)
            self.assertEqual(Path(directory, 'backup-ths-debt-1.body').read_bytes(), b'{"data": []}')

    def test_existing_attempt_never_triggers_another_request(self):
        with tempfile.TemporaryDirectory() as directory, patch('requests.Session') as session:
            Path(directory, 'backup-margin-sh-1.permit').write_text('{}')
            with self.assertRaises(FileExistsError):
                CLI.execute('backup-margin-sh', '2026-11-03', '600519', directory)
            session.assert_not_called()

    def test_reviewed_sina_move_preserves_symbol_and_uses_https(self):
        with tempfile.TemporaryDirectory() as directory:
            target = 'http://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml?symbol=600519&t1=all&p=1'
            path = self.parent(directory, 'backup-sina-reports', b'page', status=302, location=target)
            spec = follow_request('backup-sina-reports-follow', path, '2026-11-03', '600519')
            self.assertEqual(spec['url'], 'https://stock.finance.sina.com.cn/stock/go.php/vReport_List/kind/search/index.phtml')
            receipt = json.loads(Path(path).read_text())
            receipt['response']['location'] = target.replace('stock.finance.sina.com.cn', 'example.com')
            Path(path).write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, '白名单'):
                follow_request('backup-sina-reports-follow', path, '2026-11-03', '600519')

    def test_parent_bytes_must_match_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.parent(directory, 'backup-margin-sh', b'{}')
            Path(path).with_suffix('.body').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, '摘要'):
                follow_request('backup-margin-sh-page', path, '2026-11-03', '600519')

    def test_margin_follow_reads_only_missing_confirmed_second_page(self):
        with tempfile.TemporaryDirectory() as directory:
            body = json.dumps({'detailsDate': '20261103', 'pageHelp': {
                'pageNo': 1, 'pageCount': 2, 'pageSize': 2, 'total': 3, 'data': [{}, {}]}}).encode()
            path = self.parent(directory, 'backup-margin-sh', body)
            spec = follow_request('backup-margin-sh-page', path, '2026-11-03', '600519')
            self.assertEqual(spec['params']['pageHelp.pageNo'], '2')
            self.assertEqual(spec['params']['pageHelp.pageSize'], '2')

    def test_dragon_detail_is_bound_to_parent_security_date_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            anchor = "<a a-param='/ShowReport/data?SHOWTYPE=JSON&CATALOGID=1842_detal&TABKEY=tab1,tab2&DQRQ=2026-11-03&ZQDM=000417&ZBDM=0902'>详情</a>"
            payload = [{'metadata': {'catalogid': '1842_xxpl'}, 'data': [
                {'zqdm': '000417', 'dqrq': '2026-11-03', 'bz': anchor}]}]
            path = self.parent(directory, 'backup-dragon-sz', json.dumps(payload).encode(), code='300750')
            spec = follow_request('backup-dragon-sz-detail', path, '2026-11-03', '000417')
            self.assertEqual(spec['params']['ZBDM'], '0902')
            with self.assertRaisesRegex(ValueError, '父页'):
                follow_request('backup-dragon-sz-detail', path, '2026-11-03', '000001')

    def test_margin_metadata_is_same_catalog_date_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.parent(directory, 'backup-margin-sz', b'PKworkbook', code='300750')
            spec = follow_request('backup-margin-sz-meta', path, '2026-11-03', '300750')
            self.assertEqual(spec['params']['SHOWTYPE'], 'JSON')
            self.assertEqual(spec['params']['txtDate'], '2026-11-03')
            self.assertEqual(spec['params']['CATALOGID'], '1837_xxpl')


if __name__ == '__main__':
    unittest.main()
