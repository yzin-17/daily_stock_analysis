"""Immutable credential snapshots used by the ThesisLedger provider runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class ProviderCredentialSnapshot:
    provider_id: str
    source: str
    method: str | None
    values: Mapping[str, str] = field(repr=False)
    config_version: int
    credential_version: int

    @classmethod
    def create(
        cls,
        provider_id: str,
        source: str,
        method: str | None,
        values: Mapping[str, str],
        config_version: int,
        credential_version: int,
    ) -> "ProviderCredentialSnapshot":
        return cls(
            provider_id=provider_id,
            source=source,
            method=method,
            values=MappingProxyType(dict(values)),
            config_version=int(config_version),
            credential_version=int(credential_version),
        )
