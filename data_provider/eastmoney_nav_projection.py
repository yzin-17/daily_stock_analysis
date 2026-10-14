"""Eastmoney 两种 SDK 净值列名投影；不向业务暴露 DataFrame。"""

from datetime import datetime, timezone
import math

import numpy as np

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_fund_nav_contract_v1 import LatestNavResult, NavPoint
from src.services.thesis_ledger_nav_dates import nav_datetime


def nav_points(frame) -> tuple[NavPoint, ...]:
    if frame is None or getattr(frame, 'empty', True):
        raise CapabilityContractError('not_covered', 'Provider 未覆盖该基金')
    columns = set(frame.columns)
    day = next((name for name in ('净值日期', '日期', 'date', 'nav_date') if name in columns), None)
    nav = next((name for name in ('单位净值', '单位净值(元)', 'unit_nav', 'nav') if name in columns), None)
    if day is None or nav is None:
        raise CapabilityContractError('invalid_response', 'Provider 净值响应缺少必要字段')
    points, dates = [], set()
    for _, row in frame.iterrows():
        raw_date, raw_nav = row.get(day), row.get(nav)
        try:
            stamp = nav_datetime(raw_date).isoformat()
            if isinstance(raw_nav, (bool, np.bool_)):
                raise ValueError('净值不能是布尔值')
            number = float(raw_nav)
            if not math.isfinite(number) or number <= 0 or stamp in dates:
                raise ValueError('净值或日期非法/重复')
            point = NavPoint(reported_date=str(raw_date).strip()[:10], nav_date=stamp, unit_nav=number)
        except (TypeError, ValueError) as error:
            raise CapabilityContractError('invalid_response', 'Provider 净值日期或数值非法/重复') from error
        dates.add(stamp)
        points.append(point)
    if not points:
        raise CapabilityContractError('not_covered', 'Provider 未覆盖该基金')
    return tuple(points)


def latest_nav(frame, query, provider, upstream) -> LatestNavResult:
    points = nav_points(frame)
    return LatestNavResult(symbol=query.symbol, point=max(points, key=lambda point: point.nav_date),
        provider=provider, upstream_source=upstream, observed_at=datetime.now(timezone.utc).isoformat(),
        source_reported_end=max(point.reported_date for point in points))


def history_nav(frame, query, provider, upstream):
    from src.services.thesis_ledger_fund_nav_history_contract_v1 import NavHistoryResult
    points = nav_points(frame)
    dates = [datetime.fromisoformat(point.nav_date) for point in points]
    if any(first >= second for first, second in zip(dates, dates[1:])):
        raise CapabilityContractError('invalid_response', 'Provider 净值历史必须严格升序')
    selected = tuple(point for point in points
        if (query.start is None or point.reported_date >= query.start)
        and (query.end is None or point.reported_date <= query.end))
    if query.limit is not None:
        selected = selected[-query.limit:]
    if not selected:
        raise CapabilityContractError('not_covered', 'Provider 净值未覆盖请求窗口')
    return NavHistoryResult(symbol=query.symbol, points=selected, provider=provider,
        upstream_source=upstream, observed_at=datetime.now(timezone.utc).isoformat(),
        source_reported_end=max(point.reported_date for point in points))
