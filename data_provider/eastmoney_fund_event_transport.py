"""东财基金事件端点的有界传输及 JSON 字面量读取。"""

import json
from decimal import Decimal
import re
from typing import Any

import requests

ENDPOINT = "https://fund.eastmoney.com/Data/funddataIndex_Interface.aspx"


def read_literal(text: str, name: str) -> Any:
    matches = list(re.finditer(rf"\bvar\s+{re.escape(name)}\s*=\s*", text))
    if len(matches) != 1:
        raise ValueError("基金事件响应变量缺失或重复")
    body = text[matches[0].end():]
    value, end = json.JSONDecoder(parse_float=Decimal).raw_decode(body)
    if not body[end:].lstrip().startswith(";"):
        raise ValueError("基金事件响应不是独立 JSON 字面量")
    return value


def read_event_page(session: requests.Session, *, year: int, page: int,
                    data_type: str, max_bytes: int) -> str:
    if data_type not in {"8", "9"}:
        raise ValueError("基金事件端点类型无效")
    with session.get(ENDPOINT, params={
        "dt": data_type, "page": str(page), "rank": "BZDM", "sort": "asc",
        "gs": "", "ftype": "", "year": str(year),
    }, timeout=(5, 15), allow_redirects=False, stream=True) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("基金事件响应状态无效")
        chunks = bytearray()
        for chunk in response.iter_content(chunk_size=16384):
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise ValueError("基金事件响应超过大小上限")
        return chunks.decode("utf-8-sig")
