"""六目标实际工厂、标准合同、原口径/摘要及无SDK参数透传回归。"""

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES
from src.services.thesis_ledger_capability_contracts import CapabilityContractError, ReadContext
from src.services.thesis_ledger_capability_registry import CapabilityRegistry, ExactKey, QueryBinding, SourceDefinition
from src.services.thesis_ledger_public_fees_contract_v1 import CONTRACT, FeeRequest, FeeResult
from src.services.thesis_ledger_public_fees import OfficialPublicFeeProvider, DATA_PATH, ARCHIVE_DATA_PATH
from src.services.thesis_ledger_multimarket_rules import SOURCES, BASE, MultimarketRuleProvider
from src.services.thesis_ledger_provider_runtime import ThesisLedgerDataRequest, ThesisLedgerProviderRuntime, ProviderCallError
from tests.test_configured_market_routes import store, apply, no_admissions

NOW=datetime(2026,10,8,tzinfo=timezone.utc)
ROWS=list(CAPABILITY_SOURCES.routes('HISTORICAL_PUBLIC_FEES'))


def wire(key,target):
    if key['market']=='CN':
        path=ARCHIVE_DATA_PATH if target['upstreamSource'].endswith('archive') else DATA_PATH
        pack=json.loads(path.read_text());symbol='600000.SH'
    else:
        name=SOURCES[(key['market'],key['capability'],target['providerId'],target['upstreamSource'])]
        pack=json.loads((BASE/name).read_text());symbol=pack['symbol']
    return {'contractVersion':3,'consumer':'thesis-ledger','requestId':'fee-standard-contract',
        'routeKey':key,'routeTarget':{**target,'routeIndex':0},'symbol':symbol,'range':pack['coverage']}


def outer(payload,**changes):
    args=dict(instrument_type='STOCK',start=payload['range']['start'],end=payload['range']['end'],
              request_id=payload['requestId'],parameters=payload)
    args.update(changes)
    return ThesisLedgerDataRequest('HISTORICAL_PUBLIC_FEES',payload['symbol'],**args)


@pytest.mark.parametrize('key,target,binding',ROWS)
def test_actual_factory_original_payload_and_revision_are_preserved(store,key,target,binding):
    from src.services.thesis_ledger_current_data_route import _DATA_ADAPTERS,current_data_adapter_revisions
    from src.services.thesis_ledger_local_rules import LOCAL_RULE_ADAPTER_IMPORTS
    payload=wire(key,target)
    apply(store,key,target)
    runtime=ThesisLedgerProviderRuntime(store)
    adapter=runtime._adapter(target['providerId'])
    cls=OfficialPublicFeeProvider if key['market']=='CN' else MultimarketRuleProvider
    assert isinstance(adapter,cls)
    assert target['providerId'] not in LOCAL_RULE_ADAPTER_IMPORTS
    adapter.clock=lambda:NOW
    expected=cls(clock=lambda:NOW).read(payload)
    result=runtime.execute_request(outer(payload))
    assert result.value==expected and result.route_index==0 and not result.fallback_used
    assert runtime._adapter(target['providerId']) is adapter
    assert (key['market'],'STOCK',key['capability'],target['providerId'],target['upstreamSource']) not in _DATA_ADAPTERS
    revisions=current_data_adapter_revisions(key,target)
    assert revisions['adapterRevision']==binding.adapter_revision and revisions['sourceRevision']==binding.source_revision
    assert result.value['provenance']['historicalAvailability']=='notVerified'
    if target['upstreamSource']=='us-section31-exchange-t2':
        assert result.value['provenance']['chargeDatePolicy']['effectiveTradeDate']=='2024-05-20'
    if target['upstreamSource']=='us-stock-public-fees':
        taf=next(charge for charge in result.value['segments'][0]['charges'] if charge['code']=='tradingActivityFee')
        assert taf['basis']=='actualShares' and taf['maximumAmount'] and taf['lowPriceExemption']=='belowPerShareRate'
    no_admissions(store)


@pytest.mark.parametrize('change',['extra','wrong_outer_window','wrong_outer_id','wrong_capability','timeframe'])
def test_invalid_capability_parameters_do_not_read_source(store,change):
    key,target,_=ROWS[0];payload=wire(key,target);apply(store,key,target)
    calls=[];runtime=ThesisLedgerProviderRuntime(store,adapters={target['providerId']:SimpleNamespace(read=lambda _:calls.append(True))})
    options={}
    if change=='extra':payload['sdk_method']='arbitrary'
    elif change=='wrong_outer_window':options['end']='2023-08-01'
    elif change=='wrong_outer_id':options['request_id']='different-id'
    elif change=='wrong_capability':payload['routeKey']={**key,'capability':'REALTIME_QUOTE'}
    else:options['timeframe']='1d'
    with pytest.raises(ProviderCallError) as error:runtime.execute_request(outer(payload,**options))
    assert error.value.code=='invalid_request' and not calls


@pytest.mark.parametrize('mutation',['float_rate','wrong_symbol','wrong_target','gap','reference','null_optional','bad_datetime'])
def test_standard_contract_rejects_bad_source_result_without_repairing_it(mutation):
    key,target,_=ROWS[0];payload=wire(key,target)
    raw=OfficialPublicFeeProvider(clock=lambda:NOW).read(payload)
    if mutation=='float_rate':raw['segments'][0]['charges'][0]['rate']=0.001
    elif mutation=='wrong_symbol':raw['symbol']='600519.SH'
    elif mutation=='wrong_target':raw['provenance']['upstreamSource']='other'
    elif mutation=='gap':raw['segments'][0]['range']['start']='2023-08-01'
    elif mutation=='reference':raw['segments'][0]['charges'][0]['sourceDocumentIds']=['missing']
    elif mutation=='null_optional':raw['sourceDocuments'][0]['sha256']=None
    else:raw['provenance']['fetchedAt']='2026-10-08T00:00:00'
    before=deepcopy(raw)
    with pytest.raises(CapabilityContractError) as error:
        CAPABILITY_SOURCES.query(CONTRACT,FeeRequest.model_validate(payload),key,target,
            ReadContext(SimpleNamespace(read=lambda _:raw)))
    assert error.value.code=='invalid_response' and raw==before


def test_second_structured_percentage_source_uses_same_contract_without_production_registration():
    key,target,_=ROWS[0];payload=wire(key,target)
    raw=OfficialPublicFeeProvider(clock=lambda:NOW).read(payload)
    adapted_payload={**payload,'routeTarget':{'providerId':'fixture-fee','upstreamSource':'fixture','routeIndex':0}}
    other_input={'charges':raw['segments'],'percentRates':{fee['code']:str(Decimal(fee['rate'])*100) for fee in raw['segments'][0]['charges']}}
    def read(query,context):
        result=deepcopy(raw)
        result.update({field:query.model_dump()[field] for field in FeeRequest.model_fields})
        result['provenance']={**result['provenance'],'providerId':'fixture-fee','upstreamSource':'fixture'}
        for charge in result['segments'][0]['charges']:
            charge['rate']=format(Decimal(other_input['percentRates'][charge['code']])/100,'f')
        return FeeResult.model_validate(result)
    binding=QueryBinding(CONTRACT,ExactKey('CN','STOCK','HISTORICAL_PUBLIC_FEES'),'fixture','adapter-v1','source-v1',read)
    registry=CapabilityRegistry((SourceDefinition('fixture-fee',lambda:{},lambda _:object(),(binding,)),))
    value=registry.query(CONTRACT,FeeRequest.model_validate(adapted_payload),key,
        {'providerId':'fixture-fee','upstreamSource':'fixture'},ReadContext(None))
    assert value.segments[0].charges[0].rate==raw['segments'][0]['charges'][0]['rate']
    assert CAPABILITY_SOURCES.source('fixture-fee') is None
