"""账户接口访问状态；与业务数据读取及历史覆盖分开。"""

from dataclasses import dataclass
from typing import Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, model_validator
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot


class AccountAccessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    state: Literal["allowed", "permission_missing", "credential_missing", "credential_invalid", "unknown", "not_included"]
    attempted: bool
    errorCode: str | None = None

    @model_validator(mode="after")
    def actual_check(self):
        if self.state in {"allowed", "permission_missing", "credential_invalid"} and not self.attempted:
            raise ValueError("账户访问结论必须来自实际接口请求")
        if self.state == "allowed" and self.errorCode is not None:
            raise ValueError("可访问状态不能包含错误")
        if self.state == "not_included" and (self.attempted or self.errorCode not in {
                "account_level_insufficient", "independent_permission_not_enabled"}):
            raise ValueError("等级未包含的功能必须跳过请求并记录原因")
        return self


@dataclass(frozen=True)
class AccountAccessProbeContext:
    key: Mapping[str, object]
    target: Mapping[str, object]
    snapshot: ProviderCredentialSnapshot
    adapter: object
    timeout_seconds: float


@dataclass(frozen=True)
class AccountAccessLevelPolicy:
    parse: Callable[[object], dict | None]
    excluded: Callable[[Mapping, dict | None], Mapping | None]
    revision: str


def validate_account_access_policy(source):
    for callback in (source.account_access_probe, source.account_access_snapshot, source.route_requires_credential, source.account_access_group):
        if callback is not None and not callable(callback):
            raise ValueError("来源账户访问检查声明无效")
    if type(source.account_access_cooldown_seconds) is not int or not 60 <= source.account_access_cooldown_seconds <= 86400:
        raise ValueError("来源账户检查冷却必须在60到86400秒之间")
    if type(source.account_access_max_requests) is not int or not 1 <= source.account_access_max_requests <= 16:
        raise ValueError("来源账户检查次数必须在1到16之间")
    policy = source.account_access_level
    if policy is not None and (not isinstance(policy, AccountAccessLevelPolicy)
                               or not callable(policy.parse) or not callable(policy.excluded)
                               or not isinstance(policy.revision, str) or not policy.revision.strip()):
        raise ValueError("来源账号等级声明无效")
