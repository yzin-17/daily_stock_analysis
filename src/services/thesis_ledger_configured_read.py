"""普通精确读取返回前的当前策略、来源及凭证校验。"""


class ConfiguredReadError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def check_configured_read_current(store, key, target, revision, snapshot=None):
    current = store.effective_policy_v3()
    if (not isinstance(current, dict) or current.get("enabled") is not True
            or current.get("revision") != revision or current.get("sourceDesiredRevision") != revision):
        raise ConfiguredReadError("policy_not_applied", "数据返回前路由策略已变更")
    routes = [route for route in current.get("routes", []) if route.get("key") == key]
    if len(routes) != 1:
        raise ConfiguredReadError("policy_not_applied", "数据返回前精确路由已变更")
    targets = [entry for entry in routes[0].get("targets", [])
               if entry.get("providerId") == target["providerId"]
               and entry.get("upstreamSource") == target["upstreamSource"]]
    if len(targets) != 1 or targets[0].get("eligible") is not True:
        raise ConfiguredReadError("policy_not_applied", "数据返回前来源已不可执行")
    if snapshot is not None and store.provider_credential_snapshot(target["providerId"]) != snapshot:
        raise ConfiguredReadError("credential_missing", "数据返回前来源凭证已变更")
