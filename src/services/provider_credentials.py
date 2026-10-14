"""Provider credential schema, validation, and encrypted-value normalization.

This module owns the control-plane credential contract. It deliberately does
not read environment configuration or perform provider requests; callers pass
the existing encrypted value and use the normalized result for persistence and
manifest reporting.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable
from src.services.thesis_ledger_capability_sources import CAPABILITY_SOURCES


CREDENTIAL_FORMAT_VERSION = 1
STRUCTURED_CREDENTIAL_KIND = "provider_credentials"
CONFIG_PATCH_KEYS = frozenset(
    {
        "contractVersion",
        "consumer",
        "requestId",
        "enabled",
        "settings",
        "credentials",
        "clearCredentials",
        "expectedConfigVersion",
    }
)


@dataclass(frozen=True)
class CredentialDefinition:
    method: str
    fields: tuple[str, ...]


@dataclass(frozen=True)
class CredentialValue:
    """A decrypted structured credential."""

    method: str
    values: dict[str, str]


@dataclass(frozen=True)
class CredentialPatch:
    method: str
    values: dict[str, str]


PROVIDER_CREDENTIAL_DEFINITIONS: dict[str, tuple[CredentialDefinition, ...]] = {
    **{source.provider_id: tuple(CredentialDefinition(method, fields) for method, fields in source.credentials)
       for source in CAPABILITY_SOURCES.sources if source.credentials},
}


def credential_schema(provider_id: str) -> dict[str, Any]:
    methods = []
    for definition in PROVIDER_CREDENTIAL_DEFINITIONS.get(provider_id, ()):
        fields = definition.fields
        if definition.method == "oauth":
            # tokenJson is an internal SDK token payload. It is persisted only
            # by the OAuth manager and must never become a hand-editable field.
            fields = ("clientId",)
        methods.append(
            {
                "method": definition.method,
                "fields": [
                    {
                        "name": field,
                        "secret": definition.method not in {"oauth", "opend"},
                        "required": True,
                    }
                    for field in fields
                ],
            }
        )
    return {"methods": methods}


def _definition(provider_id: str, method: str) -> CredentialDefinition:
    for definition in PROVIDER_CREDENTIAL_DEFINITIONS.get(provider_id, ()):
        if definition.method == method:
            return definition
    raise ValueError(f"Provider {provider_id} 不支持凭证方式 {method}")


def parse_credential_patch(provider_id: str, raw: Any) -> CredentialPatch:
    if not isinstance(raw, dict):
        raise ValueError("credentials 必须是对象")
    unknown = set(raw) - {"method", "values"}
    if unknown:
        raise ValueError(f"credentials 包含未知字段: {', '.join(sorted(map(str, unknown)))}")
    method = raw.get("method")
    if not isinstance(method, str) or not method.strip():
        raise ValueError("credentials.method 必须是非空字符串")
    method = method.strip()
    if method == "oauth":
        raise ValueError("OAuth 凭证只能由授权内部接口写入")
    definition = _definition(provider_id, method)
    values = raw.get("values")
    if not isinstance(values, dict):
        raise ValueError("credentials.values 必须是对象")
    unknown = set(values) - set(definition.fields)
    if unknown:
        raise ValueError(f"credentials.values 包含未知字段: {', '.join(sorted(map(str, unknown)))}")
    normalized: dict[str, str] = {}
    source = CAPABILITY_SOURCES.source(provider_id)
    normalize = source.normalize_credential_value if source is not None else None
    for field, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"credentials.values.{field} 必须是字符串")
        result = normalize(field, value) if normalize is not None else value.strip()
        if not isinstance(result, str):
            raise ValueError('来源凭据字段归一化结果必须为字符串')
        normalized[field] = result
    return CredentialPatch(method=method, values=normalized)


def validate_config_patch_keys(payload: dict[str, Any]) -> None:
    unknown = set(payload) - CONFIG_PATCH_KEYS
    if unknown:
        raise ValueError(f"Provider config 包含未知字段: {', '.join(sorted(map(str, unknown)))}")
    if "enabled" in payload and not isinstance(payload["enabled"], bool):
        raise ValueError("Provider enabled 必须是布尔值")
    if "settings" in payload and not isinstance(payload["settings"], dict):
        raise ValueError("Provider settings 必须是对象")
    if "expectedConfigVersion" in payload:
        version = payload["expectedConfigVersion"]
        if not isinstance(version, int) or isinstance(version, bool) or not 0 <= version <= 9007199254740991:
            raise ValueError("expectedConfigVersion 必须是非负安全整数")


def decode_credential_plaintext(plaintext: str) -> CredentialValue:
    """Decode the current structured storage format."""

    try:
        raw = json.loads(plaintext)
    except (TypeError, ValueError) as exc:
        raise ValueError("Provider 凭证格式无效") from exc
    if not isinstance(raw, dict):
        raise ValueError("Provider 凭证格式无效")
    if raw.get("kind") == STRUCTURED_CREDENTIAL_KIND and raw.get("version") != CREDENTIAL_FORMAT_VERSION:
        raise ValueError("Provider 凭证格式版本不支持")
    if raw.get("kind") != STRUCTURED_CREDENTIAL_KIND:
        raise ValueError("Provider 凭证格式无效")
    unknown = set(raw) - {"version", "kind", "method", "values"}
    if unknown:
        raise ValueError(f"Provider 凭证包含未知字段: {', '.join(sorted(map(str, unknown)))}")
    method = raw.get("method")
    values = raw.get("values")
    if not isinstance(method, str) or not isinstance(values, dict):
        raise ValueError("Provider 凭证格式无效")
    if any(not isinstance(key, str) or not isinstance(value, str) for key, value in values.items()):
        raise ValueError("Provider 凭证字段格式无效")
    return CredentialValue(method=method, values=dict(values))


def validate_stored_credential(provider_id: str, credential: CredentialValue) -> None:
    definition = _definition(provider_id, str(credential.method or ""))
    if set(credential.values) - set(definition.fields):
        raise ValueError("Provider 凭证包含未知字段")
    missing = [field for field in definition.fields if not credential.values.get(field, "").strip()]
    if missing:
        raise ValueError(f"Provider 凭证字段未完整配置: {', '.join(missing)}")


def encode_credential_plaintext(patch: CredentialPatch) -> str:
    return json.dumps(
        {
            "version": CREDENTIAL_FORMAT_VERSION,
            "kind": STRUCTURED_CREDENTIAL_KIND,
            "method": patch.method,
            "values": patch.values,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def merge_credential_patch(
    provider_id: str,
    patch: CredentialPatch,
    existing: CredentialValue | None,
) -> CredentialValue:
    definition = _definition(provider_id, patch.method)
    if existing is None or existing.method != patch.method:
        values = dict(patch.values)
        missing = [field for field in definition.fields if not values.get(field)]
        if missing:
            raise ValueError(f"首次配置或切换凭证方式必须提供: {', '.join(missing)}")
        return CredentialValue(method=patch.method, values=values)

    values = dict(existing.values)
    for field, value in patch.values.items():
        if value:
            values[field] = value
    missing = [field for field in definition.fields if not values.get(field)]
    if missing:
        raise ValueError(f"凭证字段未完整配置: {', '.join(missing)}")
    return CredentialValue(method=patch.method, values=values)


def configured_fields(provider_id: str, credential: CredentialValue | None) -> dict[str, bool]:
    definitions = PROVIDER_CREDENTIAL_DEFINITIONS.get(provider_id, ())
    definition = next(
        (item for item in definitions if credential and item.method == credential.method),
        definitions[0] if definitions else None,
    )
    fields = set(definition.fields) if definition else set()
    if credential is None:
        return {field: False for field in sorted(fields)}
    if credential.method == "oauth":
        return {"clientId": bool(credential.values.get("clientId", ""))}
    return {field: bool(credential.values.get(field, "")) for field in sorted(fields)}


def decrypt_stored_credential(
    row: Any,
    decrypt: Callable[[str, str], str],
) -> CredentialValue | None:
    if row is None or not row["credential_ciphertext"]:
        return None
    plaintext = decrypt(str(row["secret_key_version"] or ""), str(row["credential_ciphertext"]))
    return decode_credential_plaintext(plaintext)
