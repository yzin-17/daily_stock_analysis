"""登记获准的新闻元数据准入及受控日期扩展，不写政策、凭据或业务数据。"""
import base64
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import hmac
import json
from pathlib import Path
import re
import sqlite3
import sys
from news_admission_extension import EXTENSION_FIELDS, validate_extension_options, extend_existing_news_admission
from news_admission_renewal import RENEWAL_FIELDS, validate_renewal_options, renew_existing_news_admission


KEY = {"kind": "data", "market": "US", "assetType": "STOCK", "capability": "NEWS_SEARCH"}


def news_profile(payload):
    if payload['providerId'] in {'cls', 'wallstreetcn'}:
        provider = payload['providerId']
        from data_provider.market_flash_news import CLS_URL, WSCN_URL
        return ({**KEY, 'market': 'CN', 'capability': 'NEWS_FEED'},
                {'providerId': provider, 'upstreamSource': 'cls-roll' if provider == 'cls' else 'wallstreetcn-a-stock'},
                'CN.STOCK', 'public-metadata-research', CLS_URL if provider == 'cls' else WSCN_URL)
    if payload['providerId'] == 'hithink':
        return ({**KEY, 'market': 'CN'}, {'providerId': 'hithink', 'upstreamSource': 'hithink-news-stock-timeline'},
                '600519.SH', 'account-news-metadata', 'https://fuyao.aicubes.cn/api/news/stock/timeline-list')
    if payload['providerId'] == 'yfinance':
        return (KEY, {'providerId': 'yfinance', 'upstreamSource': 'yahoo-search-news'},
                'NVDA.US', 'public-metadata-research', 'https://query2.finance.yahoo.com/v1/finance/search')
    assert payload['providerId'] == 'akshare', '未审阅的新闻来源'
    return ({**KEY, 'market': 'CN'}, {'providerId': 'akshare', 'upstreamSource': 'eastmoney'},
            '600519.SH', 'public-metadata-research', 'https://so.eastmoney.com/news/s?keyword=600519')


def parse_akshare_metadata(raw):
    import pandas as pd
    from data_provider.akshare_news import SDK_VERSIONS, SDK_FUNCTION_SHA256, project_news
    value = json.loads(raw)
    assert set(value) == {'symbol', 'sdkVersion', 'sdkFunctionSha256', 'rows'}
    assert value['symbol'] == '600519.SH' and value['sdkVersion'] in SDK_VERSIONS
    assert value['sdkFunctionSha256'] == SDK_FUNCTION_SHA256
    assert isinstance(value['rows'], list) and 1 <= len(value['rows']) <= 10
    fields = {'关键词', '新闻标题', '发布时间', '文章来源', '新闻链接'}
    assert all(isinstance(row, dict) and set(row) == fields for row in value['rows'])
    return project_news(pd.DataFrame(value['rows']), '600519')


def parse_hithink_metadata(raw):
    from data_provider.hithink_news import NEWS_URL, parse_hithink_page
    value = json.loads(raw)
    assert set(value) == {'symbol', 'endpoint', 'responseSha256', 'payload'}
    assert value['symbol'] == '600519.SH' and value['endpoint'] == NEWS_URL
    assert re.fullmatch(r'[a-f0-9]{64}', value['responseSha256'])
    payload = value['payload']
    assert set(payload) == {'code', 'data'} and set(payload['data']) == {'items', 'has_more', 'offset'}
    fields = {'id', 'title', 'source', 'publish_time_ms', 'jump_url'}
    assert all(isinstance(row, dict) and set(row) == fields for row in payload['data']['items'])
    return parse_hithink_page(json.dumps(payload, ensure_ascii=False).encode(), max_results=100).records


def parse_yahoo_metadata(raw):
    from data_provider.yahoo_news import ITEM_FIELDS, SDK_VERSION, SEARCH_URL, parse_yahoo_page
    value = json.loads(raw)
    assert set(value) == {'symbol', 'sdkVersion', 'endpoint', 'responseSha256', 'payload'}
    assert value['symbol'] == 'NVDA.US' and value['sdkVersion'] == SDK_VERSION and value['endpoint'] == SEARCH_URL
    assert re.fullmatch(r'[a-f0-9]{64}', value['responseSha256'])
    payload = value['payload']
    assert set(payload) == {'quotes', 'news'}
    assert all(isinstance(row, dict) and set(row) == {'symbol', 'quoteType'} for row in payload['quotes'])
    assert all(isinstance(row, dict) and set(row) == ITEM_FIELDS for row in payload['news'])
    return parse_yahoo_page(json.dumps(payload, ensure_ascii=False).encode(), 'NVDA.US', max_results=10).records


def parse_flash_metadata(raw, provider):
    from data_provider.market_flash_news import CLS_URL, WSCN_URL, FIELDS, UPSTREAM_COMMIT, FlashNewsSettings, parse_flash_page
    value = json.loads(raw)
    assert set(value) == {'scope', 'upstreamCommit', 'endpoint', 'responseSha256', 'payload'}
    assert value['scope'] == 'CN.STOCK' and value['upstreamCommit'] == UPSTREAM_COMMIT
    assert value['endpoint'] == (CLS_URL if provider == 'cls' else WSCN_URL)
    assert re.fullmatch(r'[a-f0-9]{64}', value['responseSha256'])
    payload = value['payload']
    assert set(payload) in ({'data'}, {'code', 'data'})
    key = 'roll_data' if provider == 'cls' else 'items'
    assert set(payload['data']) == {key}
    assert isinstance(payload['data'][key], list)
    assert all(isinstance(row, dict) and set(row) == FIELDS[provider] for row in payload['data'][key])
    return parse_flash_page(json.dumps(payload, ensure_ascii=False).encode(), provider,
                            max_results=FlashNewsSettings(provider).result_limit).records


def validate_evidence(payload, now, parse_feed):
    assert isinstance(payload, dict)
    extension = payload.get('extendExisting') is True
    renewal = payload.get('renewExisting') is True
    assert not (extension and renewal)
    assert set(payload) == {
        'mode', 'project', 'providerId', 'symbol', 'start', 'end', 'validHours',
        'recordedBy', 'observedAt', 'feedSha256', 'feedBase64', 'license',
    } | (EXTENSION_FIELDS if extension else set()) | (RENEWAL_FIELDS if renewal else set()) | (
        {'rollRecentWindow'} if renewal and 'rollRecentWindow' in payload else set())
    assert payload['project'] == 'thesis-ledger-dev' and payload['mode'] in {'apply', 'verify'}
    _key, _target, symbol, license_name, source_url = news_profile(payload)
    assert payload['symbol'] == symbol and payload['license'] == license_name
    if renewal:
        validate_renewal_options(payload)
    elif extension:
        validate_extension_options(payload)
    else:
        assert type(payload['validHours']) is int and 1 <= payload['validHours'] <= 168
    assert isinstance(payload['recordedBy'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.@/-]{0,63}', payload['recordedBy'])
    start, end = date.fromisoformat(payload['start']), date.fromisoformat(payload['end'])
    assert start.isoformat() == payload['start'] and end.isoformat() == payload['end']
    lookback_days = 7 if extension or renewal else 3
    assert now.date() - timedelta(days=lookback_days) <= start <= now.date() <= end <= now.date() + timedelta(days=7)
    observed = datetime.fromisoformat(payload['observedAt'].replace('Z', '+00:00'))
    assert observed.tzinfo is not None and timedelta(0) <= now - observed <= timedelta(hours=24)
    raw = base64.b64decode(payload['feedBase64'], validate=True)
    assert 0 < len(raw) <= 512 * 1024 and sha256(raw).hexdigest() == payload['feedSha256']
    records = parse_feed(raw)
    assert records and any(row.published_at and start <= datetime.fromisoformat(row.published_at).date() <= now.date() for row in records)
    return {"source": source_url, "observedAt": payload['observedAt'],
            "feedSha256": payload['feedSha256'], "license": payload['license'], "symbol": symbol,
            "scopeDateFrom": payload['start'], "scopeDateTo": payload['end'],
            "items": [{"title": row.title, "url": row.url, "publishedAt": row.published_at,
                       "publisher": row.publisher} for row in records]}


def assert_first_admission(connection, key, target):
    rows = connection.execute(
        "SELECT route_key_json FROM thesis_ledger_route_admission_v3 "
        "WHERE consumer='thesis-ledger' AND provider_id=? AND upstream_source=?",
        (target['providerId'], target['upstreamSource']),
    )
    if any(json.loads(row[0]) == key for row in rows):
        raise ValueError('已有新闻准入，本入口不覆盖或续期')


class FirstAdmissionConnection(sqlite3.Connection):
    route_key: dict
    route_target: dict
    credential_guard = None

    def execute(self, sql, parameters=()):
        cursor = super().execute(sql, parameters)
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            assert_first_admission(self, self.route_key, self.route_target)
            if self.credential_guard is not None and self.credential_guard() is not True:
                raise ValueError('新闻凭据在首次准入登记前已变化')
        return cursor


def insert_only_connection(database_path, key, target, credential_guard=None):
    class ScopedConnection(FirstAdmissionConnection):
        route_key = key
        route_target = target

    connection = sqlite3.connect(Path(database_path).as_uri() + '?mode=rw', uri=True, timeout=10,
                                 isolation_level=None, factory=ScopedConnection)
    connection.row_factory = sqlite3.Row
    connection.credential_guard = credential_guard
    connection.execute('PRAGMA foreign_keys=ON')
    return connection


def admission_only_store(database_path, key, target, credential_guard=None):
    from src.services.thesis_ledger_control import ThesisLedgerControlStore

    class NewsAdmissionStore(ThesisLedgerControlStore):
        def __init__(self):
            # 只读预检已有结构；避免普通构造器初始化结构或轮换其他凭据。
            self.database_path = str(database_path)

        def _connect(self):
            return insert_only_connection(database_path, key, target, credential_guard)

    return NewsAdmissionStore()


def current_credential_revision(database_path, target):
    if target['providerId'] != 'hithink':
        return 'not-required'
    from src.services.thesis_ledger_control import ThesisLedgerControlStore, _provider_credential_revision_from_snapshot

    class ReadOnlyStore(ThesisLedgerControlStore):
        def __init__(self):
            self.database_path = str(database_path)

        @contextmanager
        def _connect(self):
            connection = sqlite3.connect(Path(database_path).as_uri() + '?mode=ro', uri=True)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
            finally:
                connection.close()

    revision = _provider_credential_revision_from_snapshot(ReadOnlyStore().provider_credential_snapshot('hithink'))
    assert isinstance(revision, str) and re.fullmatch(r'hmac-sha256-v1:[a-f0-9]{64}', revision)
    return revision


def main(payload):
    key, target, symbol, _license, _url = news_profile(payload)
    if target['providerId'] in {'cls', 'wallstreetcn'}:
        from data_provider.thesis_ledger_sources.market_flash_news import ADAPTER_REVISION, PROFILES
        SOURCE_REVISION = PROFILES[target['providerId']][2]
        parser = lambda raw: parse_flash_metadata(raw, target['providerId'])
    elif target['providerId'] == 'yfinance':
        from data_provider.thesis_ledger_sources.yahoo_news import ADAPTER_REVISION, SOURCE_REVISION
        parser = parse_yahoo_metadata
    elif target['providerId'] == 'hithink':
        from data_provider.thesis_ledger_sources.hithink_news import ADAPTER_REVISION, SOURCE_REVISION
        parser = parse_hithink_metadata
    elif target['providerId'] == 'akshare':
        from data_provider.thesis_ledger_sources.akshare_news import ADAPTER_REVISION, SOURCE_REVISION
        parser = parse_akshare_metadata
    else:
        raise ValueError('未审阅的新闻来源')
    from src.services.thesis_ledger_control import _database_path

    now = datetime.now(timezone.utc)
    evidence = validate_evidence(payload, now, parser)
    configured = Path(_database_path())
    db = configured.resolve()
    assert str(db) == '/app/data/stock_analysis.db' and db.is_file() and not configured.is_symlink()
    with sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='thesis_ledger_route_admission_v3'").fetchone()
        if payload['mode'] == 'apply' and not (payload.get('extendExisting') or payload.get('renewExisting')):
            assert_first_admission(connection, key, target)
    credential_revision = current_credential_revision(db, target)
    guard = lambda: hmac.compare_digest(credential_revision, current_credential_revision(db, target))
    store = admission_only_store(db, key, target, guard)
    assert any(row['providerId'] == target['providerId'] and row['enabled'] for row in store.provider_registry())
    if payload.get('extendExisting') or payload.get('renewExisting'):
        active_guard = lambda: guard() and any(
            row['providerId'] == target['providerId'] and row['enabled'] for row in store.provider_registry())
        operation = renew_existing_news_admission if payload.get('renewExisting') else extend_existing_news_admission
        return operation(db, payload, evidence, key, target,
                         (ADAPTER_REVISION, SOURCE_REVISION), credential_revision, active_guard, now)
    existing = store.get_route_admission_v3(key=key, target=target)
    raw = json.dumps(evidence, ensure_ascii=False, sort_keys=True).encode('utf-8')
    digest = sha256(raw).hexdigest()
    directory = db.parent / 'thesis-ledger-route-evidence'
    assert not directory.is_symlink()
    path = directory / (digest + '.json')
    assert not path.is_symlink()
    if payload['mode'] == 'verify':
        assert existing and existing['recordVersion'] == 1 and existing['admissionState'] == 'admitted'
        expected = {'scopeSymbols': [symbol], 'scopeDateFrom': payload['start'], 'scopeDateTo': payload['end'],
                    'adapterRevision': ADAPTER_REVISION, 'sourceRevision': SOURCE_REVISION,
                    'credentialRevision': credential_revision, 'recordedBy': payload['recordedBy'],
                    'evidenceSha256': digest, 'evidenceRef': 'sha256:' + digest}
        assert all(existing[key] == value for key, value in expected.items())
        assert path.is_file() and path.read_bytes() == raw
        return {'mode': 'verify', 'status': 'admitted', 'recordVersion': 1, 'writes': False,
                'evidenceSha256': digest, 'validUntil': existing['validUntil'], 'symbol': symbol}
    assert existing is None, '已有准入须单独审阅，禁止覆盖或续期'
    directory.mkdir(exist_ok=True, mode=0o700)
    if path.exists():
        assert path.read_bytes() == raw
    else:
        with path.open('xb') as output:
            output.write(raw)
        path.chmod(0o600)
    assert path.read_bytes() == raw
    record = store.record_route_admission_v3(
        key=key, target=target, evidence_ref='sha256:' + digest, evidence_sha256=digest,
        scope_symbols=[symbol], scope_date_from=payload['start'], scope_date_to=payload['end'],
        adapter_revision=ADAPTER_REVISION, source_revision=SOURCE_REVISION, credential_revision=credential_revision,
        valid_from=now.isoformat(), valid_until=(now + timedelta(hours=payload['validHours'])).isoformat(),
        recorded_by=payload['recordedBy'],
    )
    assert record['recordVersion'] == 1 and record['evidenceSha256'] == digest
    return {"status": "admitted", "providerId": target['providerId'], "symbol": symbol, "evidenceSha256": digest,
            "evidencePath": str(path), "scopeDateFrom": payload['start'], "scopeDateTo": payload['end'],
            "validUntil": record['validUntil'], "recordVersion": record['recordVersion']}


if __name__ == '__main__':
    print(json.dumps(main(json.load(sys.stdin)), ensure_ascii=False))
