"""固定请求与禁止任意来源的离线验证。"""

import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('source_check', Path(__file__).with_name('astock-existing-source-check.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SourceCheckTest(unittest.TestCase):
    def value(self, profile='report-eps'):
        return {'profile': profile, 'symbol': '600519.SH', 'start': '2026-09-21', 'end': '2026-09-25'}

    def test_rejects_arbitrary_target_and_fields(self):
        for value in [self.value('unknown'), self.value() | {'url': 'https://example.com'}, self.value() | {'symbol': '600519'}]:
            with self.assertRaises(ValueError):
                module.validate_input(value)

    def test_exact_report_page_and_identity(self):
        url, params, headers = module.request_spec(self.value())
        self.assertEqual(url, 'https://reportapi.eastmoney.com/report/list')
        self.assertEqual((params['code'], params['pageNo'], params['pageSize']), ('600519', '1', '2'))
        self.assertNotIn('Authorization', headers)

    def test_source_cases_do_not_escape_existing_publishers(self):
        domains = {'reportapi.eastmoney.com', 'quotes.sina.cn', 'datacenter-web.eastmoney.com',
                   'np-weblist.eastmoney.com', 'push2his.eastmoney.com', 'stock.gtimg.cn', 'push2.eastmoney.com',
                   '17.push2.eastmoney.com', 'vip.stock.finance.sina.com.cn', 'qt.gtimg.cn',
                   'push2ex.eastmoney.com', 'mobappconfig.securities.eastmoney.com', 'api-one-wscn.awtmt.com',
                   'datacenter.eastmoney.com'}
        from urllib.parse import urlparse
        for profile in module.PROFILES:
            if profile == 'valuation':
                continue
            url, _, _ = module.request_spec(self.value(profile))
            self.assertIn(urlparse(url).hostname, domains)

    def test_global_news_retains_required_trace_parameter(self):
        from uuid import UUID
        _, params, _ = module.request_spec(self.value('global-news'))
        self.assertEqual(str(UUID(params['req_trace'])), params['req_trace'])

    def test_company_events_remain_single_security_single_page(self):
        for profile, (_, identity, _) in module.DATA_CENTER.items():
            _, params, _ = module.request_spec(self.value(profile))
            if identity is None:
                self.assertNotIn('filter', params)
            else:
                self.assertEqual(params['filter'], f'({identity}="600519")')
            self.assertEqual((params['pageNumber'], params['pageSize']), ('1', '5'))


if __name__ == '__main__':
    unittest.main()
