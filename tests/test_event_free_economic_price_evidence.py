"""经济资格只能来自完整事件流及固定窗口，不能从空数组或原生qfq猜测。"""
import copy
from datetime import datetime
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

path = Path(__file__).parents[1]/'src/services/event_free_economic_price_evidence.py'
spec = importlib.util.spec_from_file_location('event_free', path)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class EventFreeEvidence(unittest.TestCase):
    def setUp(self):
        stamp = lambda day: int(datetime.fromisoformat(day).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()*1000)
        self.at = '2026-11-06T14:30:00.000Z'
        self.dates = ['2026-10-29', '2026-10-30']
        actions = {'thscode': '600519.SH', 'ticker': '600519', 'item': [
            {'ticker': '600519', 'ex_date_ms': stamp('2026-06-01'), 'dividend_per_share': 1, 'per_share_bonus': 0}]}
        bars = [{'date_ms': stamp(d), 'open_price': c, 'high_price': c+1,
                 'low_price': c-1, 'close_price': c, 'volume': 100, 'turnover': c*100}
                for d,c in zip(self.dates,[100,105])]
        self.value = {'symbol':'600519.SH','start':self.dates[0],'end':self.dates[-1],
            'transportAndProtocolPassed':True,'providerStateUnchanged':True,'reads':[
                {'kind':'corporate_actions','endpoint':'https://fuyao.aicubes.cn/api/a-share/corporate-actions/adjustment-factors',
                 'parameters':{'thscode':'600519.SH'},'httpStatus':200,'completedAt':self.at,
                 'response':{'code':0,'data':actions}},
                {'kind':'raw_prices','endpoint':'https://fuyao.aicubes.cn/api/a-share/prices/historical',
                 'parameters':{'thscode':'600519.SH','adjust':'none','interval':'1d',
                   'start':stamp('2026-10-29'),'end':stamp('2026-10-31')-1},'httpStatus':200,
                 'completedAt':self.at,'response':{'code':0,'data':{'item':bars}}}]}

    def qualify(self):
        with patch.object(m, 'DOCUMENT_SHA256', m.hashlib.sha256(b'doc').hexdigest()):
            return m.qualify(self.value,b'doc',self.dates,capture_sha256='a'*64,qualified_at=self.at)

    def test_fixed_event_free_window_has_independent_economic_return(self):
        value=self.qualify()
        self.assertEqual(value['independentReturn'],'0.05')
        self.assertEqual(value['anchorDate'],'2026-10-30')
        self.assertEqual(value['qualification']['windowEventCount'],0)

    def test_any_event_inside_window_rejected_even_zero_cash(self):
        row=self.value['reads'][0]['response']['data']['item'][0]
        row['ex_date_ms']=self.value['reads'][1]['response']['data']['item'][0]['date_ms']
        row['dividend_per_share']=0
        with self.assertRaises(ValueError):self.qualify()

    def test_empty_or_filtered_event_stream_is_not_qualified(self):
        original=copy.deepcopy(self.value)
        self.value['reads'][0]['response']['data']['item']=[]
        with self.assertRaises(ValueError):self.qualify()
        self.value=original
        self.value['reads'][0]['parameters']['from']='2026-10-29'
        with self.assertRaises(ValueError):self.qualify()

    def test_wrong_identity_or_qfq_input_rejected(self):
        original=copy.deepcopy(self.value)
        self.value['reads'][0]['response']['data']['thscode']='000001.SZ'
        with self.assertRaises(ValueError):self.qualify()
        self.value=original
        self.value['reads'][1]['parameters']['adjust']='forward'
        with self.assertRaises(ValueError):self.qualify()

    def test_missing_session_or_future_observation_rejected(self):
        original=copy.deepcopy(self.value)
        self.value['reads'][1]['response']['data']['item'].pop()
        with self.assertRaises(ValueError):self.qualify()
        self.value=original
        self.value['reads'][1]['completedAt']='2026-11-07T00:00:00Z'
        with self.assertRaises(ValueError):self.qualify()


if __name__=='__main__':unittest.main()
