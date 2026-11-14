"""固定衍生品采证参数及父子证据身份的离线验证。"""

import json
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from derivative_reference_source_cases import request_spec, follow_request


class SourceCases(unittest.TestCase):
    def test_exchange_date_path(self):
        self.assertIn('20261103', request_spec('future-shfe', '2026-11-03', '600519')['url'])
        self.assertIn('www.ine.cn', request_spec('rank-ine', '2026-11-03', '600519')['url'])
        self.assertIn('/202611/03/index.xml', request_spec('future-cffex-xml', '2026-11-03', '600519')['url'])

    def test_fixed_gfex_post(self):
        spec = request_spec('future-gfex', '2026-11-03', '600519')
        self.assertEqual(spec['method'], 'POST')
        self.assertEqual(spec['data'], {'trade_date': '20261103', 'trade_type': '0'})

    def test_reject_arbitrary_inputs(self):
        for profile, code in [('future-quote', 'RB0&list=secret'), ('future-kline', 'RB2613'),
                              ('option-months', '600519'), ('rank-cffex', '../IF'), ('a50-quote', 'OTHER')]:
            with self.assertRaises(ValueError):
                request_spec(profile, '2026-11-03', code)

    def test_quotes_and_history_separate(self):
        self.assertIn('nf_M0', request_spec('future-quote', '2026-11-03', 'M0')['url'])
        self.assertEqual(request_spec('future-kline', '2026-11-03', 'M0')['params'], {'symbol': 'M0'})

    def test_convertible_page_only(self):
        spec = request_spec('convertible-bonds', '2026-11-03', '600519')
        self.assertEqual(spec['params']['pageSize'], '20')
        self.assertIn('CURRENT_BOND_PRICE', spec['params']['quoteColumns'])

    def parent(self, root, profile, code, content):
        p = Path(root) / 'source.json'
        p.with_suffix('.body').write_bytes(content)
        p.write_text(json.dumps({'profile': profile, 'code': code, 'date': '2026-11-03',
                                'response': {'httpStatus': 200, 'sha256': sha256(content).hexdigest()}}))
        return p

    def test_option_month_identity(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.parent(root, 'option-months', '510050', b'{"result":{"status":{"code":0},"data":{"stockId":"510050","contractMonth":["2026-11","2026-12"]}}}')
            self.assertIn('OP_DOWN_5100502611', follow_request('option-chain', p, '2026-11-03', '510050:2611')['url'])
            for code in ['510300:2611', '510050:2703']:
                with self.assertRaises(ValueError):
                    follow_request('option-chain', p, '2026-11-03', code)

    def test_months_reject_default_underlying(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.parent(root, 'option-months', '588000', b'{"result":{"status":{"code":0},"data":{"stockId":"510050","contractMonth":["2026-11"]}}}')
            with self.assertRaises(ValueError):
                follow_request('option-chain', p, '2026-11-03', '588000:2611')

    def test_option_code_identity_and_digest(self):
        with tempfile.TemporaryDirectory() as root:
            p = self.parent(root, 'option-chain', '510050:2611', b'var hq_str_OP_UP_5100502611="CON_OP_10009999,";')
            self.assertIn('CON_SO_10009999', follow_request('option-values', p, '2026-11-03', '10009999')['url'])
            with self.assertRaises(ValueError):
                follow_request('option-values', p, '2026-11-03', '10008888')
            p.with_suffix('.body').write_bytes(b'CON_OP_10009999')
            with self.assertRaises(ValueError):
                follow_request('option-values', p, '2026-11-03', '10009999')


if __name__ == '__main__':
    unittest.main()
