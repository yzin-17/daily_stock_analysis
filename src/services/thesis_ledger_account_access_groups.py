"""来源声明的同接口权限事实复用，不推断业务数据覆盖。"""

from copy import deepcopy
import json


def route_id(route):
    return json.dumps([route["key"], route["target"]], sort_keys=True)


class PermissionGroups:
    def __init__(self, source, previous, cooling):
        self.group = source.account_access_group
        self.facts = {}
        partial = previous and any(r["access"]["state"] == "unknown" for r in previous["routes"])
        if not self.group or not partial:
            return
        for route in previous["routes"]:
            access = route["access"]
            known = access["state"] not in {"unknown", "not_included"}
            if known or cooling({"checkedAt": previous["checkedAt"], "routes": [route]}):
                self.remember(route, access)

    def get(self, entry):
        if not self.group:
            return None
        fact = self.facts.get(self.group(entry["key"]))
        if not fact:
            return None
        access = deepcopy(fact[1])
        if access["state"] == "unknown" and fact[0] != route_id(entry):
            access["attempted"] = False
        return access

    def remember(self, entry, access):
        if self.group and access["state"] != "not_included":
            self.facts[self.group(entry["key"])] = (route_id(entry), deepcopy(access))

    def stops_source(self, access):
        if access.get("errorCode") == "rate_limited_global":
            return True
        return access.get("errorCode") == "rate_limited" and not self.group
