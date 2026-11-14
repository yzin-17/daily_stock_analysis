"""公开路径采证的白名单、次数和失败分类离线检查。"""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from hashlib import sha256
import json
from public_reference_source_links import follow_request

PATH = Path(__file__).with_name('public-reference-source-check.py')
SPEC = importlib.util.spec_from_file_location('public_source_check', PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PublicReferenceSourceCheck(unittest.TestCase):
    def test_rejects_arbitrary_endpoints_and_identity(self):
        for profile, day, code in [('https://example.com', '2026-11-03', '600519'),
                                   ('fr', '2026-02-30', '600519'),
                                   ('fr', '2026-11-03', '../600519')]:
            with self.assertRaises(ValueError):
                MODULE.request_spec(profile, day, code)

    def test_protocols_are_distinct_and_queries_remain_fixed(self):
        package = MODULE.request_spec('tdx-package', '2026-11-03', '600519')
        self.assertEqual(package['url'], 'https://www.tdx.com.cn/products/data/data/g4day/20261103.zip')
        self.assertEqual(MODULE.request_spec('fr', '2026-11-03', '600519')['url'].split('/')[-1], 'frr-chrt.csv')
        query = MODULE.request_spec('sse-etf', '2026-11-03', '600519')
        self.assertEqual(query['params']['STAT_DATE'], '2026-11-03')
        self.assertEqual(query['method'], 'GET')

    def test_does_not_promote_error_pages_or_follow_redirects(self):
        outcomes = {401: 'permission-denied', 403: 'permission-denied', 429: 'rate-limited',
                    302: 'redirect-needs-review', 404: 'http-error'}
        for code, outcome in outcomes.items():
            self.assertEqual(MODULE.response_outcome(code, 'text/html', b'<html>error'), outcome)
        self.assertEqual(MODULE.response_outcome(200, 'text/html', b'<html>challenge'), 'html-needs-parsing')

    def test_does_not_overwrite_permission_before_network(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, 'fr-1.permit').write_text('{}')
            with self.assertRaises(FileExistsError):
                MODULE.execute('fr', '2026-11-03', '600519', directory)

    def test_discovered_links_require_unchanged_origin_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, 'nbs-index-1.json')
            body = b'<a href="https://example.com/evil">2026&#24180;&#37319;&#36141;&#32463;&#29702;&#25351;&#25968;</a>'
            path.with_suffix('.body').write_bytes(body)
            path.write_text(json.dumps({'profile': 'nbs-index', 'response': {
                'httpStatus': 200, 'sha256': sha256(body).hexdigest(), 'truncated': False,
                'url': 'https://www.stats.gov.cn/sj/zxfb/'}}))
            with self.assertRaisesRegex(ValueError, 'HTTPS'):
                follow_request('nbs-article', str(path), '2026-11-03', '600519')
            path.with_suffix('.body').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, '原件'):
                follow_request('nbs-article', str(path), '2026-11-03', '600519')


if __name__ == '__main__':
    unittest.main()
