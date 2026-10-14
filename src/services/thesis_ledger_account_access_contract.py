"""账户接口访问状态；与业务数据读取及历史覆盖分开。"""

from dataclasses import dataclass
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, model_validator
from src.services.provider_credentials_runtime import ProviderCredentialSnapshot


class AccountAccessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    state: Literal["allowed", "permission_missing", "credential_missing", "credential_invalid", "unknown"]
    attempted: bool
    errorCode: str | None = None

    @model_validator(mode="after")
    def actual_check(self):
        if self.state in {"allowed", "permission_missing", "credential_invalid"} and not self.attempted:
            raise ValueError("账户访问结论必须来自实际接口请求")
        if self.state == "allowed" and self.errorCode is not None:
            raise ValueError("可访问状态不能包含错误")
        return self


@dataclass(frozen=True)
class AccountAccessProbeContext:
    key: Mapping[str, object]
    target: Mapping[str, object]
    snapshot: ProviderCredentialSnapshot
    adapter: object
    timeout_seconds: float
