"""在完整应用响应验收后发布能力验证，读取前固定可能执行的来源上下文。"""

from collections.abc import Mapping
import os

from .thesis_ledger_capability_verification import begin_verification, publish_verified


class VerificationRequest:
    def __init__(self, runtime, key, *, request_id=None, symbol=None):
        self.runtime, self.key, self.attempts, self.revision = runtime, dict(key), {}, None
        self.request_id, self.symbol = request_id, symbol
        store = getattr(runtime, 'store', None)
        if (not callable(getattr(store, 'provider_credential_snapshot', None))
                or not callable(getattr(store, '_connect', None))
                or os.getenv('THESIS_LEDGER_FIXTURE_MODE', '').lower() in {'true', '1', 'yes', 'on'}):
            return
        policy = store.effective_policy_v3()
        if not isinstance(policy, Mapping) or policy.get('enabled') is not True:
            return
        self.revision = policy.get('revision')
        for route in policy.get('routes', []):
            if route.get('key') != self.key:
                continue
            for target in route.get('targets', []):
                if target.get('eligible') is True:
                    identity = target['providerId'], target['upstreamSource']
                    self.attempts[identity] = begin_verification(store, self.key, target)

    def accept(self, response, *, provider=None, source=None):
        """调用者保证完整合同已通过；此处只检查结果与冻结的执行上下文。"""
        if not self.attempts:
            return response
        value = response if isinstance(response, Mapping) else {}
        if value.get('status') in {'unavailable', 'unsupported', 'unknown', 'partial', 'not_covered', 'not-covered', 'blocked', 'failed', 'error'}:
            return response
        provenance = value.get('provenance') or {}
        identity = provider or provenance.get('providerId'), source or provenance.get('upstreamSource')
        attempt = self.attempts.get(identity)
        if attempt is None:
            return response
        policy = self.runtime.store.effective_policy_v3()
        if not isinstance(policy, Mapping) or policy.get('enabled') is not True or policy.get('revision') != self.revision:
            return response
        if not any(route.get('key') == self.key and any(
            target.get('eligible') is True and (target.get('providerId'), target.get('upstreamSource')) == identity
            for target in route.get('targets', [])
        ) for route in policy.get('routes', [])):
            return response
        proof = {
            'requestId': value.get('requestId') or self.request_id, 'symbol': value.get('symbol') or self.symbol,
            'inputFingerprint': value.get('inputFingerprint'), 'provenance': provenance,
            'result': value,
        }
        publish_verified(self.runtime.store, attempt, evidence=proof)
        return response
