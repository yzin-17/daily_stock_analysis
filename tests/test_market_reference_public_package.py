"""盘后文件布局、数据损坏与来源覆盖的边界。"""

import io
import struct
import zipfile

import pytest

from data_provider.market_reference_public_package import parse_market, parse_package, asset_kind


def market_record(code='600000', sequence=0):
    cod, block = bytearray(150), bytearray(512)
    cod[:6] = code.encode()
    struct.pack_into('<H', cod, 32, sequence)
    name = '测试证券'.encode('gbk')
    cod[40:40 + len(name)] = name
    struct.pack_into('<d', block, 4, 9.5)
    struct.pack_into('<4d', block, 12, 10, 11, 9, 10.25)
    struct.pack_into('<Q', block, 56, 12345)
    struct.pack_into('<d', block, 72, 126536.25)
    return bytes(cod), bytes(block)


@pytest.mark.parametrize('code,market,kind', [('600000', 'sh', '股票'), ('159915', 'sz', 'ETF'),
                                           ('920000', 'bj', '股票'), ('110001', 'sh', None)])
def test_asset_scope_excludes_convertibles_and_distinguishes_share_units(code, market, kind):
    assert asset_kind(code, market) == kind


def test_market_parses_unsigned_share_quantity_and_price_offsets():
    cod, block = market_record()
    records, count = parse_market(cod, block, 'sh', '2026-11-03')
    assert count == 1 and records[0]['volume'] == '12345'
    assert records[0]['close'] == '10.25' and records[0]['amount'] == '126536.25'


@pytest.mark.parametrize('fault', ['truncated', 'sequence', 'duplicate', 'nonfinite'])
def test_market_rejects_corrupt_layout_and_duplicate_identity(fault):
    cod, block = market_record()
    if fault == 'truncated':
        cod = cod[:-1]
    elif fault == 'sequence':
        cod, _ = market_record(sequence=1)
    elif fault == 'duplicate':
        cod, block = cod + cod, block + block
    else:
        raw = bytearray(block)
        struct.pack_into('<d', raw, 72, float('nan'))
        block = bytes(raw)
    with pytest.raises(ValueError):
        parse_market(cod, block, 'sh', '2026-11-03')


def test_package_does_not_call_a_small_valid_zip_a_full_market_package():
    cod, block = market_record()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('sh261103.cod', cod)
        archive.writestr('sh261103.md1', block)
    with pytest.raises(ValueError, match='有价记录不足'):
        parse_package(output.getvalue(), '2026-11-03')


def test_wrong_day_package_does_not_become_an_empty_result():
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('sh261102.cod', b'')
    with pytest.raises(ValueError):
        parse_package(output.getvalue(), '2026-11-03')
