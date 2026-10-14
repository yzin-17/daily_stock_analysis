"""旧固定业务样本的新版传输包装；不用于真实来源验收。"""

from copy import deepcopy
import hashlib
import json
from data_provider.hithink_fund_dividend_semantics import RESEARCH_RECORDS_SEMANTICS


def research_source_fixture(value):
    result = deepcopy(value)
    body = json.dumps({"code": 0, "data": {"item": result["items"]}})
    result.update(researchSemantics=RESEARCH_RECORDS_SEMANTICS, transportCountVerified=True,
                  rawBusinessResponse=body, responseSha256=hashlib.sha256(body.encode()).hexdigest(),
                  rawItemCount=len(result["items"]))
    return result
