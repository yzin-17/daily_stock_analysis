"""功能路由的启停与旧 V3 序列化兼容；不决定来源、准入或用户策略迁移。"""

from .thesis_ledger_status_capabilities_v1 import NAMES


def validate_feature_route_shape(route, fail, request_id):
    if not isinstance(route, dict) or not {"key", "targets"} <= set(route) <= {"key", "targets", "enabled"}:
        fail("INVALID_POLICY_SCHEMA", "V3 route 只允许 key、targets 和可选 enabled", request_id)
    if "enabled" in route and type(route["enabled"]) is not bool:
        fail("INVALID_POLICY_SCHEMA", "功能 enabled 必须是布尔值", request_id)
    key = route.get("key")
    if isinstance(key, dict) and key.get("capability") in NAMES and "enabled" not in route:
        fail("INVALID_POLICY_SCHEMA", "新增独立功能须明确启停状态", request_id)


def canonical_feature_route(original, key, targets):
    result = {"key": key, "targets": targets}
    if "enabled" in original:
        result["enabled"] = original["enabled"]
    return result


def effective_feature_route(original, targets, reason):
    return {**canonical_feature_route(original, original["key"], targets), "reason": reason}


def feature_target_reason(policy, route, read_reason):
    if not policy["enabled"] or route.get("enabled") is False:
        return "disabled"
    return read_reason()


def feature_provider_epoch(store, provider_ids):
    """无凭据结果也必须隔离来源启停往返和设置变化；只读取控制版本。"""
    selected = set(provider_ids)
    rows = {row["providerId"]: row for row in store.provider_registry() if row["providerId"] in selected}
    if set(rows) != selected or any(type(row.get("configVersion")) is not int for row in rows.values()):
        from .thesis_ledger_current_data_route import CurrentDataRouteError
        raise CurrentDataRouteError("not_admitted", "来源控制版本缺失")
    return {provider: (row["configVersion"], row["enabled"]) for provider, row in rows.items()}
