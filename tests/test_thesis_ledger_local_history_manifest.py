import json
from pathlib import Path

from src.services.thesis_ledger_local_history_manifest import attach_local_history
from src.services.thesis_ledger_notice_dividends import build_notice_dividend_manifest
from src.services.thesis_ledger_public_fees import build_public_fee_manifest
from src.services.thesis_ledger_trading_status import build_trading_status_manifest
from src.services.thesis_ledger_provider_capability_projection import exact_provider_manifests
from src.services.thesis_ledger_multimarket_rules import multimarket_rule_manifests


def test_metadata_matches_all_registered_reader_files():
    root = Path(__file__).resolve().parents[1] / 'data_provider'
    paths = {
        'cn-stock-auction-fees': root / 'public_fees/cn-stock-auction-2023-08.json',
        'cn-stock-auction-fees-archive': root / 'public_fees/cn-stock-auction-archive-2023-08.json',
        'cn-stock-daily-suspension': root / 'trading_status/000995-2023-08-suspension.json',
        'cn-stock-risk-price-limits': root / 'trading_status/000995-2023-08-risk-limits.json',
        'cn-stock-initial-listing': root / 'trading_status/603350-2024-07-listing.json',
        'cn-stock-delisting-period': root / 'trading_status/600462-2025-07-delisting.json',
        'cn-etf-cash-notices': root / 'cash_distributions/510300-2023-01.json',
        'hkex-trading-fee': root / 'market_reference/hk-00147-fees.json',
        'hk-stock-public-fees': root / 'market_reference/hk-00147-public-fees.json',
        'hkex-00147-halt': root / 'market_reference/hk-00147-status.json',
        'us-section31-exchange-t2': root / 'market_reference/us-gme-fees.json',
        'us-stock-public-fees': root / 'market_reference/us-gme-public-fees.json',
        'nyse-gme-halt-observations': root / 'market_reference/us-gme-status.json',
    }
    manifests = [build_public_fee_manifest(), build_trading_status_manifest(), build_notice_dividend_manifest()]
    projected = exact_provider_manifests({**{item['providerId']: item for item in manifests}, **multimarket_rule_manifests()})
    seen = set()
    for manifest in projected.values():
        assert manifest['sourceKind'] == 'local_history'
        for source in manifest['upstreamSources']:
            pack = json.loads(paths[source['sourceId']].read_text())
            data = source['localHistory']
            assert data['datasetId'] == pack['datasetId']
            assert data['coverage'] == {key: pack['coverage'][key] for key in ('start', 'end')}
            assert data['symbols'] == ([pack['symbol']] if 'symbol' in pack else [])
            assert data.get('scope') == pack.get('scope')
            assert data['updateMode'] == 'bundled'
            assert data['documents'] == [{key: document[key] for key in ('id', 'title', 'url', 'publishedDate') if key in document and document[key] is not None}
                                         for document in pack['sourceDocuments']]
            assert not any(value in source['displayName'] for value in ('皇台', '沪深300', '00147', 'GME'))
            seen.add(source['sourceId'])
    assert seen == set(paths)


def test_missing_and_invalid_pack_keep_local_kind_without_inventing_coverage(tmp_path):
    for content in [None, '{}', '{broken', '{"datasetId":"x","coverage":{"start":"2023-02-01","end":"2023-01-01"},"symbol":"x"}']:
        path = tmp_path / 'pack.json'
        if content is not None:
            path.write_text(content)
        manifest = attach_local_history({'upstreamSources': [{'sourceId': 'local'}]}, {'local': path})
        assert manifest['sourceKind'] == 'local_history'
        assert 'localHistory' not in manifest['upstreamSources'][0]
