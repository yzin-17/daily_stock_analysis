"""从来源注册及当前有效准入投影推荐依据，不推断付费或字母优先级。"""


def source_recommendation(registry, key, target, *, read_evidence=False):
    source = registry.source(target['providerId'])
    binding = registry.resolve(key, target)
    if source is None or binding is None:
        return None
    result = {}
    group = dict(source.upstream_groups).get(target['upstreamSource'])
    if group:
        result.update(upstreamGroup=group, reason='来源注册声明的服务上游归属')
    priority = source.recommendation_priority(binding.key, target['upstreamSource']) if source.recommendation_priority else None
    if priority is not None:
        if (not isinstance(priority, tuple) or len(priority) != 2 or type(priority[0]) is not int
                or priority[0] < 0 or not isinstance(priority[1], str) or not priority[1].strip()):
            raise ValueError('来源用途优先级声明无效')
        result.update(priority=priority[0], reason=priority[1])
    # 只消费目录已核验的既有准入，不额外查找或尝试上游。
    if read_evidence is True:
        result.update(readEvidence=True, reason=result.get('reason', '已有与当前修订相符的有效读取准入'))
    return result or None
