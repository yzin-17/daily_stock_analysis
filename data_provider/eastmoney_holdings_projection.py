"""Eastmoney SDK 持仓列名、季度和明确百分比的标准投影。"""

from datetime import datetime, timezone

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_fund_holdings_contract_v1 import Holding, HoldingsResult, holdings_digest
from src.services.thesis_ledger_holding_rows import holding_period, validate_holding_rows


def holding_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if symbol.isdigit() and len(symbol) == 6:
        if symbol.startswith(('00', '30')):
            return symbol + '.SZ'
        if symbol.startswith(('92', '43', '81', '82', '83', '87', '88')):
            return symbol + '.BJ'
        return symbol + '.SH'
    return symbol


def holdings_result(frame, request, provider, upstream) -> HoldingsResult:
    if frame is None or getattr(frame, 'empty', True):
        raise CapabilityContractError('not_covered', 'Provider 未返回基金持仓披露')
    try:
        validate_holding_rows(frame)
        rows = [(holding_period(row['季度']), row) for _, row in frame.iterrows()]
        latest = max(period for period, _ in rows)
        holdings = tuple(sorted((Holding(symbol=holding_symbol(row['股票代码']), name=row['股票名称'].strip(),
            weight=float(row['占净值比例']) / 100) for period, row in rows if period == latest),
            key=lambda holding: (-holding.weight, holding.symbol)))
        attrs = getattr(frame, 'attrs', {})
        return HoldingsResult(symbol=request.symbol, report_period=f'{latest[0]}-Q{latest[1]}', disclosure_date=None,
            holdings=holdings, provider=provider, upstream_source=upstream, observed_at=datetime.now(timezone.utc).isoformat(),
            source_endpoint=attrs.get('sourceEndpoint'), source_query_year=attrs.get('sourceQueryYear'),
            source_observed_at=attrs.get('sourceObservedAt'), evidence_version=holdings_digest(holdings))
    except (ValueError, TypeError) as error:
        raise CapabilityContractError('invalid_response', str(error)) from error
