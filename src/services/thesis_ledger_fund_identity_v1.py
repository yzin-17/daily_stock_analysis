"""基金能力共用的稳定身份；不表示来源独立回显已核验。"""

import re


def fund_symbol(value: str) -> str:
    symbol = value.strip().upper()
    if re.fullmatch(r'[0-9]{6}', symbol):
        symbol += '.OF'
    if not re.fullmatch(r'[0-9]{6}\.OF', symbol):
        raise ValueError('基金需要六位代码 .OF')
    return symbol
