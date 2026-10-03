"""基金持仓报告期和原始百分比完整性边界。"""

import math
import re


def holding_period(value) -> tuple[int, int]:
    periods = re.findall(r'(?<![0-9])([0-9]{4})年\s*([1-4])季度', str(value))
    if len(periods) != 1:
        raise ValueError('Provider 基金持仓报告期无法唯一识别')
    return tuple(map(int, periods[0]))


def validate_holding_rows(frame) -> None:
    if not {'股票代码', '股票名称', '占净值比例', '季度'}.issubset(set(frame.columns)):
        raise ValueError('Provider 基金持仓响应缺少必要字段')
    seen, totals = set(), {}
    for _, row in frame.iterrows():
        symbol, name = row.get('股票代码'), row.get('股票名称')
        if not isinstance(symbol, str) or not symbol.strip() or not isinstance(name, str) or not name.strip():
            raise ValueError('Provider 基金持仓身份缺失')
        period = holding_period(row.get('季度'))
        identity = (period, symbol.strip())
        if identity in seen:
            raise ValueError('Provider 基金持仓同报告期代码重复')
        seen.add(identity)
        value = row.get('占净值比例')
        try:
            weight = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError('Provider 基金持仓权重非法') from exc
        if isinstance(value, bool) or not math.isfinite(weight) or not 0 <= weight <= 100:
            raise ValueError('Provider 基金持仓权重超出范围')
        totals[period] = totals.get(period, 0) + weight
        if totals[period] > 100.0001:
            raise ValueError('Provider 基金持仓报告期权重合计超出范围')
