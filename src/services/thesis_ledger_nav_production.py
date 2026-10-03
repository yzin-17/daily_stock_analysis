"""精确 NAV 生产编排：身份、原文、规则、日期及读取前后准入复核。"""

from datetime import datetime, timezone

from data_provider.eastmoney_fund_identity import read_fund_identity, identity_from_raw
from data_provider.eastmoney_fund_nav import read_fund_nav_evidence
from data_provider.eastmoney_nav_evidence import hash_raw, decode_source_json
from src.services.thesis_ledger_nav_admission import admitted_nav_state, nav_admission_snapshot
from src.services.thesis_ledger_nav_calendar import build_nav_research_calendar
from src.services.thesis_ledger_nav_projection import project_nav_evidence
from src.services.thesis_ledger_nav_request import (
    parse_nav_request, NAV_ADAPTER_REVISION, NAV_SOURCE_REVISION, NavProductionError,
)
from src.services.thesis_ledger_nav_rules import build_domestic_nav_rule, read_qdii_nav_rule, _instant


def execute_nav_request(runtime, payload, *, check_security=None,
                        identity_reader=None, nav_reader=None, qdii_reader=None):
    request = parse_nav_request(payload)
    if check_security:
        check_security()
    before = admitted_nav_state(runtime, request)
    cutoff = _instant(request['dataAsOf'])
    try:
        decision = decode_source_json(request['calendarDecisionRaw'])
        if (not isinstance(decision, dict) or set(decision) != {
            'schemaVersion', 'symbol', 'basis', 'configuredAt', 'decision',
        } or decision['schemaVersion'] != 'nav-research-calendar-decision-v1'
                or decision['basis'] != 'nav-dates-xshg-intersection-v1'
                or not isinstance(decision['decision'], str) or not decision['decision'].strip()
                or decision.get('symbol') != request['symbol']
                or _instant(decision['configuredAt']) > cutoff):
            raise ValueError()
        if request['fundType'] == 'domestic':
            rule = build_domestic_nav_rule(
                request['domesticRuleDecisionRaw'], evidence_ref=(
                    f"research-config://nav-domestic/{hash_raw(request['domesticRuleDecisionRaw'])}"),
                data_as_of=request['dataAsOf'],
            )
        else:
            rule = None
    except (ValueError, KeyError, TypeError):
        raise NavProductionError('invalid_request') from None
    try:
        identity = (identity_reader or read_fund_identity)(request['symbol'][:6])
        checked = identity_from_raw(identity.response_raw, request['symbol'][:6], identity.captured_at)
        if identity != checked or identity.fund_type != request['fundType']:
            raise NavProductionError('identity_mismatch')
        if _instant(identity.captured_at) > cutoff:
            raise NavProductionError('future_evidence')
        source = (nav_reader or read_fund_nav_evidence)(request['symbol'][:6])
        if rule is None:
            from data_provider.efunds_nav_disclosure import EFUNDS_NAV_RULES
            spec = EFUNDS_NAV_RULES.get(request['symbol'])
            if spec is None:
                raise NavProductionError('rule_unavailable')
            preceding = sorted(record.valuation_date for record in source.records
                               if spec.audited_start <= record.valuation_date < request['start'])
            if len(preceding) < request['warmupPeriods']:
                raise NavProductionError('insufficient_evidence')
            rule = (qdii_reader or read_qdii_nav_rule)(
                request['symbol'], start=preceding[-request['warmupPeriods']],
                end=request['end'], data_as_of=request['dataAsOf'],
            )
        calendar = build_nav_research_calendar(
            source, rule, request['calendarDecisionRaw'], mode=request['visibilityMode'],
            start=request['start'], end=request['end'], warmup_periods=request['warmupPeriods'],
            tail_trading_days=request['tailTradingDays'], data_as_of=request['dataAsOf'],
        )
    except NavProductionError:
        raise
    except (ValueError, KeyError, TypeError):
        raise NavProductionError('insufficient_evidence') from None
    except Exception:
        raise NavProductionError('upstream_failure') from None
    bounds = calendar.calendar['coverage']
    after = admitted_nav_state(runtime, request, first=bounds['startDate'], last=bounds['endDate'])
    if before != after:
        raise NavProductionError('policy_not_applied')
    if check_security:
        check_security()
    captured = datetime.now(timezone.utc)
    if captured > cutoff:
        raise NavProductionError('future_evidence')
    try:
        projected = project_nav_evidence(request, source, identity, rule, calendar, captured.isoformat())
    except (ValueError, IndexError, KeyError):
        raise NavProductionError('invalid_response') from None
    return {'contractVersion': 3, 'requestId': request['requestId'], 'symbol': request['symbol'],
            'routeKey': request['routeKey'], 'routeTarget': request['routeTarget'],
            'desiredRevision': request['desiredRevision'], 'effectivePolicyRevision': request['effectivePolicyRevision'],
            'catalogRevision': request['catalogRevision'], 'dataAsOf': request['dataAsOf'],
            'coverage': {'complete': True, 'startDate': bounds['startDate'], 'endDate': request['end']},
            'source': {'adapterRevision': NAV_ADAPTER_REVISION, 'sourceRevision': NAV_SOURCE_REVISION,
                       'providerRevision': source.reader_revision, 'credentialRevision': 'not-required',
                       'responseHash': hash_raw(projected['responseRaw']), 'capturedAt': captured.isoformat()},
            'admission': nav_admission_snapshot(after['admission']), **projected}
