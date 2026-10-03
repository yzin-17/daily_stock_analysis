"""东方财富净值原文证据；来源总量不代表历史可见性或基金日历完整。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


def hash_raw(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


@dataclass(frozen=True)
class NavRawPage:
    page_index: int
    raw_response: str
    content_hash: str


@dataclass(frozen=True)
class NavRawRecord:
    valuation_date: str
    unit_nav: str
    page_index: int
    native_record_raw: str
    content_hash: str


@dataclass(frozen=True)
class NavRawEvidence:
    fund_code: str
    endpoint: str
    reader_revision: str
    captured_at: str
    total_count: int
    pages: tuple[NavRawPage, ...]
    records: tuple[NavRawRecord, ...]
    content_hash: str


def decode_source_json(raw: str):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('基金净值原文包含重复 JSON 字段')
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError('基金净值原文包含非法 JSON 数值')

    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


def native_record_fragments(raw: str) -> tuple[str, ...]:
    """按 JSON 语法定位顶层 Datas，保留逐条原文字段顺序与数值词法。"""
    decoder = json.JSONDecoder()

    def skip(index):
        while index < len(raw) and raw[index] in ' \t\r\n':
            index += 1
        return index

    cursor = skip(skip(0) + 1)
    while raw[cursor] != '}':
        key, cursor = decoder.raw_decode(raw, cursor)
        start = skip(skip(cursor) + 1)
        _, end = decoder.raw_decode(raw, start)
        if key == 'Datas':
            if raw[start] != '[':
                raise ValueError('基金净值原文 Datas 不是数组')
            fragments = []
            position = skip(start + 1)
            while raw[position] != ']':
                _, finish = decoder.raw_decode(raw, position)
                fragments.append(raw[position:finish])
                position = skip(finish)
                if raw[position] == ',':
                    position = skip(position + 1)
            return tuple(fragments)
        cursor = skip(end)
        if raw[cursor] == ',':
            cursor = skip(cursor + 1)
    raise ValueError('基金净值原文缺少 Datas')


def parse_nav_record(item, page_index: int, native_raw: str) -> NavRawRecord:
    if not isinstance(item, dict):
        raise ValueError('基金净值行无效')
    day = item.get('FSRQ')
    if not isinstance(day, str) or len(day) != 10 or date.fromisoformat(day).isoformat() != day:
        raise ValueError('基金净值日期无效')
    nav = item.get('DWJZ')
    if not isinstance(nav, str) or not re.fullmatch(r'(?:0|[1-9][0-9]*)(?:\.[0-9]+)?', nav):
        raise ValueError('基金净值必须是来源正十进制字符串')
    if Decimal(nav) <= 0:
        raise ValueError('基金净值必须大于零')
    return NavRawRecord(day, nav, page_index, native_raw, hash_raw(native_raw))


def evidence_hash(fund_code: str, endpoint: str, revision: str, pages: list[NavRawPage]) -> str:
    return hash_raw(canonical_json({
        'fundCode': fund_code, 'endpoint': endpoint, 'readerRevision': revision,
        'pages': [{'pageIndex': p.page_index, 'contentHash': p.content_hash} for p in pages],
    }))


def validate_nav_raw_evidence(evidence: NavRawEvidence) -> None:
    """重新核对来源页与逐条投影，避免日期生产消费被改动的证据包。"""
    if (not isinstance(evidence, NavRawEvidence) or type(evidence.total_count) is not int
            or not 0 < evidence.total_count <= 100000 or not evidence.pages
            or len(evidence.pages) > 100 or len(evidence.records) != evidence.total_count
            or not re.fullmatch(r'[0-9]{6}', evidence.fund_code)
            or sum(len(page.raw_response.encode('utf-8')) for page in evidence.pages) > 32 * 1024 * 1024):
        raise ValueError('净值日期生产需要非空完整来源证据')
    if evidence.content_hash != evidence_hash(
        evidence.fund_code, evidence.endpoint, evidence.reader_revision, evidence.pages,
    ):
        raise ValueError('净值来源整批摘要不符')
    records = []
    for index, page in enumerate(evidence.pages, 1):
        if (page.page_index != index or hash_raw(page.raw_response) != page.content_hash
                or len(page.raw_response.encode('utf-8')) > 4 * 1024 * 1024):
            raise ValueError('净值来源页序或摘要不符')
        payload = decode_source_json(page.raw_response)
        if (not isinstance(payload, dict) or payload.get('Success') is not True
                or type(payload.get('TotalCount')) is not int
                or payload['TotalCount'] != evidence.total_count
                or payload.get('ErrCode') != 0 or str(payload.get('ErrorCode')) != '0'):
            raise ValueError('净值来源页状态或总量不符')
        items = payload.get('Datas')
        if not isinstance(items, list) or len(items) != min(1000, evidence.total_count - len(records)):
            raise ValueError('净值来源页长度不符')
        fragments = native_record_fragments(page.raw_response)
        if len(fragments) != len(items):
            raise ValueError('净值来源页原文数量不符')
        records.extend(parse_nav_record(item, index, raw) for item, raw in zip(items, fragments))
    if tuple(records) != evidence.records or len({r.valuation_date for r in records}) != len(records):
        raise ValueError('净值来源逐条投影或日期唯一性不符')
