"""港美精确公共费项、独立进位和实际股数维度。"""

from decimal import Decimal
import re

_FEES = {
    "handlingFee": ("both", "HKD", "turnover"),
    "stampDuty": ("both", "HKD", "turnover"),
    "afrcLevy": ("both", "HKD", "turnover"),
    "tradingActivityFee": ("sell", "USD", "actualShares"),
}
_SOURCE_CODES = {
    "hkex-trading-fee": {"handlingFee"},
    "hk-stock-public-fees": {"handlingFee", "stampDuty", "regulatoryFee", "afrcLevy"},
    "us-section31-exchange-t2": {"regulatoryFee"},
    "us-stock-public-fees": {"regulatoryFee", "tradingActivityFee"},
}


def _decimal(value, positive=False):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d+)?", value):
        raise ValueError("公共费项必须保存普通十进制字符串")
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0 or (positive and amount <= 0):
        raise ValueError("公共费项金额无效")
    return amount


def validate_multimarket_fee_charges(pack, charges, ids):
    expected = _SOURCE_CODES.get(pack["upstreamSource"])
    codes = [fee["code"] for fee in charges]
    if expected is None or set(codes) != expected or len(codes) != len(set(codes)):
        raise ValueError("公共收费来源费项缺失、重复或未经核实")
    common = {"code", "side", "currency", "rate", "basis", "sourceDocumentIds"}
    for fee in charges:
        code = fee["code"]
        spec = _FEES.get(code)
        if code == "regulatoryFee":
            spec = ("both", "HKD", "turnover") if pack["market"] == "HK" else ("sell", "USD", "turnover")
        if (fee["side"], fee["currency"], fee["basis"]) != spec:
            raise ValueError("费项/买卖侧/币种/基数错误")
        _decimal(fee["rate"], positive=code == "tradingActivityFee")
        if not fee["sourceDocumentIds"] or any(ref not in ids for ref in fee["sourceDocumentIds"]):
            raise ValueError("收费依据缺失")
        if code == "stampDuty":
            if set(fee) != common | {"amountRounding"} or fee["amountRounding"] != {"mode": "ceil", "decimalPlaces": 0}:
                raise ValueError("港股印花税必须独立整港元进位")
        elif code == "tradingActivityFee":
            if set(fee) != common | {"maximumAmount", "lowPriceExemption"} or fee["lowPriceExemption"] != "belowPerShareRate":
                raise ValueError("TAF上限与低价豁免必须完整")
            _decimal(fee["maximumAmount"], positive=True)
        elif set(fee) != common:
            raise ValueError("其他费项不得混入未核实的进位、上限或基数")
