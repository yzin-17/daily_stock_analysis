"""解析 rankhandler 的受限数据声明；不执行上游 JavaScript。"""

from dataclasses import dataclass
import json
import re


@dataclass(frozen=True)
class FundCatalogPage:
    page: int
    page_size: int
    total: int
    pages: int
    rows: tuple[tuple[str, str], ...]


def parse_fund_catalog_page(text, *, page, page_size):
    if (type(page) is not int or page < 1 or type(page_size) is not int
            or not 1 <= page_size <= 1000 or not isinstance(text, str)
            or len(text.encode('utf-8')) > 8 * 1024 * 1024):
        raise ValueError('invalid_catalog_page')
    envelope = re.fullmatch(r'\s*var\s+rankData\s*=\s*\{(.*)\}\s*;\s*', text, re.S)
    if envelope is None:
        raise ValueError('invalid_catalog_envelope')
    body, offset, fields = envelope[1], 0, {}
    allowed = {'datas', 'allRecords', 'pageIndex', 'pageNum', 'allPages', 'allNum',
               'etf_count', 'zs_count', 'gp_count', 'hh_count', 'zq_count', 'qdii_count', 'fof_count'}
    decoder = json.JSONDecoder()
    while offset < len(body):
        key = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*', body[offset:])
        if key is None or key[1] not in allowed or key[1] in fields:
            raise ValueError('invalid_catalog_field')
        offset += key.end()
        try:
            value, consumed = decoder.raw_decode(body[offset:])
        except (ValueError, RecursionError):
            raise ValueError('invalid_catalog_value') from None
        fields[key[1]] = value
        offset += consumed
        separator = re.match(r'\s*(,)?\s*', body[offset:])
        offset += separator.end()
        if offset < len(body) and not separator[1]:
            raise ValueError('invalid_catalog_separator')
        if offset == len(body) and separator[1]:
            raise ValueError('invalid_catalog_separator')
    required = {'datas', 'allRecords', 'pageIndex', 'pageNum', 'allPages'}
    if not required <= fields.keys():
        raise ValueError('missing_catalog_pagination')
    if any(type(value) is not int or value < 0 for key, value in fields.items() if key != 'datas'):
        raise ValueError('invalid_catalog_count')
    total, pages = fields['allRecords'], fields['allPages']
    if (not 1 <= total <= 100000 or fields['pageIndex'] != page or fields['pageNum'] != page_size
            or pages != (total + page_size - 1) // page_size or page > pages):
        raise ValueError('inconsistent_catalog_pagination')
    values = fields['datas']
    if not isinstance(values, list) or len(values) != min(page_size, total - (page - 1) * page_size):
        raise ValueError('incomplete_catalog_page')
    rows, identities = [], set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError('invalid_catalog_row')
        parts = value.split(',', 2)
        if len(parts) != 3 or not re.fullmatch(r'[0-9]{6}', parts[0]) or not parts[1].strip():
            raise ValueError('invalid_catalog_identity')
        code, name = parts[0], parts[1].strip()
        if code in identities:
            raise ValueError('duplicate_catalog_identity')
        identities.add(code)
        rows.append((code, name))
    return FundCatalogPage(page, page_size, total, pages, tuple(rows))
