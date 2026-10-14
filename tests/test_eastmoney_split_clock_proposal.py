"""原159516证据的离线时钟反例；候选采集协议尚未实现。"""

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

from pydantic import ValidationError
import pytest

from data_provider.eastmoney_split_diagnostics import safe_failure
from src.services.thesis_ledger_event_split_contract_v1 import CONTRACT, SplitRequest
from src.services.thesis_ledger_event_v3_adapters import EVENT_TARGET, SPLIT_KEY, SPLIT_REVISIONS
from src.services.thesis_ledger_mapping_evidence_store import MappingEvidenceStore
from src.services import thesis_ledger_split_event_reader_v3 as mapped
from tests.test_eastmoney_split_diagnostics import forbid_network as offline_network_fixture

forbid_network = offline_network_fixture
DIGEST = 'b1a4f03f94faf27acf95d2bef9d508ec06eabec1280e04c921bb677154f5fe8e'
CUTOFF = '2026-10-12T10:57:44.604Z'
COMPLETED = '2026-10-12T10:57:45.100Z'
OBSERVED = '2026-10-12T10:57:45.004Z'


@pytest.mark.integration
def test_offline_candidate_preserves_observation_and_requires_explicit_new_envelope(tmp_path, monkeypatch):
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.fromisoformat(COMPLETED.replace('Z', '+00:00'))
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(mapped, 'datetime', FixedClock)
    evidence_path = (Path(__file__).resolve().parents[2] / 'thesis-ledger/docs/tasks/evidence'
                     / '2026-10-02-m22-159516-split-mapping.json')
    if not evidence_path.is_file():
        pytest.skip('此离线候选对照需要配对工作区中的原159516映射，不以其他夹具替代')
    original = evidence_path.read_bytes()
    assert len(original) == 936 and sha256(original).hexdigest() == DIGEST
    evidence = MappingEvidenceStore(tmp_path / 'thesis-ledger-mapping-evidence')
    assert evidence.put(original) == f'sha256:{DIGEST}'
    # 原准入回执的脱敏合同字段，只用于离线模型；不写Control库或续签准入。
    admission = {'consumer': 'thesis-ledger', 'routeKey': SPLIT_KEY, 'target': EVENT_TARGET,
                 'status': 'admitted', 'admissionState': 'admitted', 'recordVersion': 1,
                 'evidenceRef': f'sha256:{DIGEST}', 'evidenceSha256': DIGEST,
                 'scopeSymbols': ['159516.SZ'], 'scopeDateFrom': '2026-03-30', 'scopeDateTo': '2026-07-10',
                 **SPLIT_REVISIONS, 'validFrom': '2026-10-12T10:53:01.391764+00:00',
                 'validUntil': '2026-10-13T10:53:01.391764+00:00',
                 'recordedAt': '2026-10-12T10:53:01.403099+00:00',
                 'invalidatedAt': None, 'invalidationReason': None}
    query = {'contractVersion': 3, 'requestId': 'ed92afdb-5f4b-4d6c-8f95-d8acf8dc08cf',
             'symbol': '159516.SZ', 'routeKey': SPLIT_KEY, 'routeTarget': {**EVENT_TARGET, 'routeIndex': 0},
             'desiredRevision': 112, 'effectivePolicyRevision': 112, 'catalogRevision': 4494082701661225,
             'start': '2026-03-30', 'end': '2026-07-10', 'dataAsOf': CUTOFF}
    saved_query = deepcopy(query)
    reader = Mock(return_value={'providerRevision': 'offline-clock-fixture', 'observations': [
        {'symbol': '159516.SZ', 'sourceConversionDate': day, 'sourceRatioPerUnit': '2',
         'endpoint': 'fund_cf_em', 'upstreamSource': 'eastmoney', 'observedAt': OBSERVED}
        for day in ['2026-03-27', '2026-07-09']]})
    raw = mapped.read_mapped_split_events(str(tmp_path / 'control.sqlite'), query, admission, reader=reader)
    response = {**query, **raw, 'fetchedAt': COMPLETED, 'admission': admission,
                'coverage': {'complete': False, 'reason': 'historical_coverage_unverified'}}
    with pytest.raises(ValidationError) as failure:
        CONTRACT.validate_result(response)
    diagnostic = safe_failure('response_validation', failure.value, query['requestId'], response)
    assert diagnostic['availableAfterCutoffCount'] == 2
    # 独立候选模型以真实完成时刻为最终截止；这不是对既有线上响应的改写。
    candidate = CONTRACT.validate_result({**response, 'dataAsOf': COMPLETED})
    assert [fact.availableAt for fact in candidate.facts] == [OBSERVED, OBSERVED]
    assert candidate.dateMappingEvidence.content.encode() == original
    assert candidate.coverage.complete is False and query == saved_query
    assert datetime.fromisoformat(COMPLETED.replace('Z', '+00:00')) < datetime(2026, 10, 12, 10, 58, 44, 604000, timezone.utc)
    # 原固定截止的相关性校验继续拒绝候选结果，不能暗改dataAsOf冒充原请求成功。
    with pytest.raises(ValueError, match='响应未匹配请求'):
        CONTRACT.correlate_result(SplitRequest.model_validate(query), candidate, EVENT_TARGET)
    reader.assert_called_once_with('159516.SZ', start='2026-03-27', end='2026-07-10')
