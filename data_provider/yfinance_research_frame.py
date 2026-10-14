"""Yahoo既有零量元信息适配；坏观察拒绝，原帧和冻结格式保持。"""

from decimal import Decimal
import math
import re

from src.services.thesis_ledger_capability_contracts import ResearchFrameContext, ResearchFrameProjection


def project_normalized(context: ResearchFrameContext) -> ResearchFrameProjection:
    return ResearchFrameProjection(_exclude_zero_volume(context.frame))


def project_hk_share_window(context: ResearchFrameContext) -> ResearchFrameProjection:
    if context.symbol != '00147.HK':
        return ResearchFrameProjection(context.frame)
    return project_normalized(context)


def _exclude_zero_volume(frame):
    if frame.attrs.get('excluded_zero_volume_observations'):
        return frame
    excluded = {}
    digest = frame.attrs.get('responseSha256', '')
    for _, row in frame.iterrows():
        volume = row.get('volume')
        if volume is None or float(volume) != 0:
            continue
        fields = {key: float(row[key]) for key in ('open', 'high', 'low', 'close')}
        if (not re.fullmatch(r'[a-f0-9]{64}', digest) or any(not math.isfinite(value) or value <= 0 for value in fields.values())
                or fields['high'] < max(fields.values()) or fields['low'] > min(fields.values())):
            raise ValueError('零量观察的OHLC或原响应摘要无效')
        amount = row.get('amount')
        if amount is not None and not math.isnan(float(amount)) and float(amount) != 0:
            raise ValueError('零量与成交额观察矛盾')
        day = str(row['date'])[:10]
        if day in excluded:
            raise ValueError('零量来源观察日期重复')
        excluded[day] = {**{name: str(Decimal(str(value))) for name, value in fields.items()},
                         'volume': '0', 'sourceResponseSha256': digest}
    if not excluded:
        return frame
    result = frame[~frame['date'].map(lambda value: str(value)[:10]).isin(excluded)].copy()
    result.attrs['excluded_zero_volume_observations'] = excluded
    return result
