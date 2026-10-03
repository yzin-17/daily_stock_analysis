"""精确 NAV 请求与错误合同；不选择备用来源或推测基金类型。"""

from copy import deepcopy
import re

from src.services.thesis_ledger_nav_rules import _instant, _range


NAV_KEY = {'kind': 'data', 'market': 'CN', 'assetType': 'MUTUAL_FUND', 'capability': 'FUND_NAV_HISTORY'}
NAV_TARGET = {'providerId': 'efinance', 'upstreamSource': 'eastmoney'}
NAV_ADAPTER_REVISION = 'efinance-fund-nav-raw-v1'
NAV_SOURCE_REVISION = 'eastmoney-fund-nav-raw-v1'


class NavProductionError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def parse_nav_request(value):
    fields = {'contractVersion', 'requestId', 'symbol', 'fundType', 'routeKey', 'routeTarget',
              'desiredRevision', 'effectivePolicyRevision', 'catalogRevision', 'start', 'end',
              'dataAsOf', 'warmupPeriods', 'tailTradingDays', 'visibilityMode',
              'calendarDecisionRaw', 'domesticRuleDecisionRaw'}
    if not isinstance(value, dict) or set(value) != fields:
        raise NavProductionError('invalid_request')
    if type(value['contractVersion']) is not int or value['contractVersion'] != 3:
        raise NavProductionError('invalid_request')
    if (not isinstance(value['requestId'], str) or not value['requestId'].strip()
            or value['requestId'].strip() != value['requestId'] or len(value['requestId']) > 128
            or not isinstance(value['symbol'], str) or not re.fullmatch(r'[0-9]{6}\.OF', value['symbol'])):
        raise NavProductionError('invalid_request')
    target = value['routeTarget']
    if (value['routeKey'] != NAV_KEY or not isinstance(target, dict)
            or set(target) != {'providerId', 'upstreamSource', 'routeIndex'}
            or {key: target[key] for key in NAV_TARGET} != NAV_TARGET):
        raise NavProductionError('not_adapted')
    for name in ['desiredRevision', 'effectivePolicyRevision', 'catalogRevision', 'warmupPeriods', 'tailTradingDays']:
        if type(value[name]) is not int or value[name] <= 0:
            raise NavProductionError('invalid_request')
    if (value['warmupPeriods'] > 100000 or value['tailTradingDays'] > 104
            or type(target['routeIndex']) is not int or target['routeIndex'] not in (0, 1)):
        raise NavProductionError('invalid_request')
    if value['desiredRevision'] != value['effectivePolicyRevision']:
        raise NavProductionError('policy_not_applied')
    if value['visibilityMode'] == 'strict-publication':
        raise NavProductionError('publication_unavailable')
    if (value['visibilityMode'] != 'research-assumption' or not isinstance(value['fundType'], str)
            or value['fundType'] not in {'domestic', 'qdii'}):
        raise NavProductionError('invalid_request')
    for name in ['calendarDecisionRaw', 'domesticRuleDecisionRaw']:
        raw = value[name]
        if raw is None and name == 'domesticRuleDecisionRaw' and value['fundType'] == 'qdii':
            continue
        if not isinstance(raw, str) or not raw or len(raw.encode('utf-8')) > 65536:
            raise NavProductionError('invalid_request')
    if value['fundType'] == 'qdii' and value['domesticRuleDecisionRaw'] is not None:
        raise NavProductionError('invalid_request')
    try:
        _range(value['start'], value['end'])
        _instant(value['dataAsOf'])
    except (ValueError, TypeError, AttributeError):
        raise NavProductionError('invalid_request') from None
    return deepcopy(value)
