"""当前规则读取复用完整来源控制修订，防止报价分发丢失按需核查上下文。"""

from src.services.thesis_ledger_capability_contracts import ReadContext
from src.services.thesis_ledger_feature_policy import feature_provider_epoch


def current_rule_context(runtime, provider, adapter):
    policy = runtime.store.effective_policy_v3()
    epoch = feature_provider_epoch(runtime.store, [provider])

    def current():
        after = runtime.store.effective_policy_v3()
        fields = ('revision', 'sourceDesiredRevision', 'enabled')
        return (
            bool(policy.get('enabled')) and after is not None
            and all(after.get(field) == policy.get(field) for field in fields)
            and feature_provider_epoch(runtime.store, [provider]) == epoch and epoch[provider][1]
        )
    return ReadContext(
        adapter, database_path=getattr(runtime.store, 'database_path', None), timeout_seconds=10,
        publish_guard=current, control_epoch=(policy['revision'], policy['sourceDesiredRevision'], epoch[provider][0]))
