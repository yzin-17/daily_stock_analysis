"""富途股票归一化研究的复合当前身份观察；历史单位与上市日保持未知。"""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from src.services.thesis_ledger_capability_contracts import CapabilityContractError
from src.services.thesis_ledger_research_instrument_contract import ResearchInstrumentFact

DATA_PATH = Path(__file__).with_name('market_reference') / 'futu-research-identities.json'
MARKETS = {'HK': ('HKD', 'Asia/Hong_Kong', 'HK_MAINBOARD', 'HKG'),
           'US': ('USD', 'America/New_York', 'US_NASDAQ', 'NMS')}


def observed_identity(symbol, market=None, data_as_of=None):
    try:
        raw = DATA_PATH.read_bytes()
        records = json.loads(raw)['identities']
        matches = [row for row in records if row['symbol'] == symbol]
        if len(matches) != 1:
            return None
        row = matches[0]
        market = market or row['market']
        currency, zone, futu_exchange, identity_exchange = MARKETS[market]
        static, identity = row['futu'], row['identity']
        meta = identity['metadata']
        if (row['market'] != market or static['symbol'] != symbol or static['market'] != market
                or static['assetType'] != 'STOCK' or static['provider'] != 'futu'
                or static['exchange'] != futu_exchange or identity['publisher'] != 'Yahoo Finance'
                or (meta['currency'], meta['exchangeTimezoneName'], meta['exchangeName'])
                != (currency, zone, identity_exchange) or meta['instrumentType'] != 'EQUITY'):
            return None
        if market == 'HK':
            if (not re.fullmatch(r'\d{5}\.HK', symbol)
                    or not re.fullmatch(r'\d{1,5}\.HK', meta['symbol'])
                    or int(symbol[:-3]) != int(meta['symbol'][:-3])):
                return None
        elif symbol != meta['symbol'] + '.US':
            return None
        if any(not re.fullmatch(r'[a-f0-9]{64}', row[field])
               for field in ('futuReceiptSha256', 'identityReceiptSha256')):
            return None
        clocks = [datetime.fromisoformat(source['observedAt']) for source in (static, identity)]
        cutoff = data_as_of or datetime.now(timezone.utc)
        if cutoff.utcoffset() is None or any(t.utcoffset() is None or t > cutoff for t in clocks):
            return None
        return {'symbol': symbol, 'market': market, 'currency': currency,
                'observedAt': max(clocks).isoformat(),
                'revision': 'futu-current-identity-sha256:' + sha256(raw).hexdigest()}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def supports_symbol(symbol, market=None):
    return observed_identity(symbol, market) is not None


def calendar_eligible(context):
    return context.asset_type == 'STOCK' and observed_identity(context.symbol, context.market) is not None


def read_instrument(request):
    item = observed_identity(request.symbol, request.market, request.data_as_of)
    if item is None:
        raise CapabilityContractError('not_covered', '富途当前身份未覆盖该证券、市场或冻结截点')
    return ResearchInstrumentFact.model_validate({
        'symbol': item['symbol'], 'market': item['market'], 'instrumentType': 'STOCK',
        'currency': item['currency'], 'lotSize': None, 'tickSize': None, 'tradable': False,
        'executionRules': {'status': 'unavailable',
                           'reason': '富途身份与 Yahoo 当前币种交叉观察；历史整手、价位及制度未核实'},
        'provider': 'futu', 'providerRevision': item['revision'],
        'occurredAt': item['observedAt'], 'availableAt': item['observedAt'],
    })
