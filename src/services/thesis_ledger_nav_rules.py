"""生产现行净值研究规则；基金日期与来源 PIT 准入由各自入口负责。"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import re

from data_provider.eastmoney_nav_evidence import canonical_json, decode_source_json, hash_raw
from data_provider.efunds_nav_disclosure import EFUNDS_NAV_RULES, read_verified_nav_document


@dataclass(frozen=True)
class NavResearchRuleEvidence:
    rule_raw: str
    document_raw: bytes
    document_kind: str
    captured_at: str
    reader_revision: str

    @property
    def rule(self) -> dict:
        return {**json.loads(self.rule_raw), 'contentHash': hash_raw(self.rule_raw)}


def _instant(value: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])', value,
    ):
        raise ValueError('规则时间必须是带时区且精度不超过微秒的 ISO 时刻')
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def _range(start: str, end: str) -> dict:
    for day in [start, end]:
        if not isinstance(day, str) or len(day) != 10 or date.fromisoformat(day).isoformat() != day:
            raise ValueError('规则适用日期无效')
    if start > end:
        raise ValueError('规则适用日期顺序无效')
    return {'startDate': start, 'endDate': end}


def _symbol(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{6}\.OF', value):
        raise ValueError('净值规则基金标的无效')


def build_domestic_nav_rule(decision_raw: str, *, evidence_ref: str,
                            data_as_of: str) -> NavResearchRuleEvidence:
    """消费明确声明普通基金的研究配置；精确生产入口必须另核验基金身份。"""
    cutoff = _instant(data_as_of)
    if not isinstance(decision_raw, str) or len(decision_raw.encode('utf-8')) > 65536:
        raise ValueError('普通研究配置原文无效或超出预算')
    decision = decode_source_json(decision_raw)
    fields = {'schemaVersion', 'symbol', 'fundType', 'delayWorkdays', 'applicableRange',
              'configuredAt', 'decision'}
    if not isinstance(decision, dict) or set(decision) != fields:
        raise ValueError('普通基金研究配置缺失或包含未知字段')
    _symbol(decision['symbol'])
    if (decision['symbol'] in EFUNDS_NAV_RULES or decision['fundType'] != 'domestic'
            or decision['schemaVersion'] != 'nav-research-default-v1'
            or type(decision['delayWorkdays']) is not int or decision['delayWorkdays'] != 1):
        raise ValueError('普通默认必须显式为 T+1，已核查 QDII 不能使用普通默认')
    interval = decision['applicableRange']
    if not isinstance(interval, dict) or set(interval) != {'startDate', 'endDate'}:
        raise ValueError('普通研究配置适用范围无效')
    _range(interval['startDate'], interval['endDate'])
    configured_at = _instant(decision['configuredAt'])
    if configured_at > cutoff:
        raise ValueError('研究规则在冻结时点尚未配置')
    if (not isinstance(decision['decision'], str) or not decision['decision'].strip()
            or not isinstance(evidence_ref, str) or not evidence_ref.startswith('research-config://')
            or len(evidence_ref) <= len('research-config://')
            or evidence_ref.strip() != evidence_ref):
        raise ValueError('普通研究默认必须绑定明确用户决策及研究配置引用')
    rule_raw = canonical_json({
        'id': f"nav-domestic-default:{decision['symbol']}", 'version': 'user-t1-default-v1',
        'symbol': decision['symbol'], 'fundType': 'domestic', 'applicableRange': interval,
        'delayWorkdays': 1, 'basis': 'domestic-default', 'evidenceRef': evidence_ref,
        'documentHash': hash_raw(decision_raw), 'configuredAt': decision['configuredAt'],
    })
    captured = datetime.now(timezone.utc)
    if captured > cutoff:
        raise ValueError('研究配置采集晚于冻结时点')
    return NavResearchRuleEvidence(rule_raw, decision_raw.encode('utf-8'), 'research-config',
                                   captured.isoformat(), 'nav-user-default-v1')


def read_qdii_nav_rule(symbol: str, *, start: str, end: str,
                       data_as_of: str) -> NavResearchRuleEvidence:
    _symbol(symbol)
    cutoff = _instant(data_as_of)
    interval = _range(start, end)
    spec = EFUNDS_NAV_RULES.get(symbol)
    if spec is None:
        raise ValueError('QDII 净值规则尚未经基金级审查')
    if start < spec.audited_start or end > spec.audited_end:
        raise ValueError('请求区间超出 QDII 规则审查范围')
    document = read_verified_nav_document(spec)
    if _instant(document.captured_at) > cutoff:
        raise ValueError('QDII 规则文档采集晚于冻结时点')
    rule_raw = canonical_json({
        'id': f'nav-disclosure:{symbol}', 'version': spec.version, 'symbol': symbol,
        'fundType': 'qdii', 'applicableRange': interval, 'delayWorkdays': spec.delay_workdays,
        'basis': 'verified-fund-rule', 'evidenceRef': spec.url,
        'documentHash': document.content_hash, 'configuredAt': document.captured_at,
    })
    return NavResearchRuleEvidence(rule_raw, document.raw, 'fund-prospectus',
                                   document.captured_at, document.reader_revision)
