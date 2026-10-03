"""Request-boundary tests for configured prompt-cache keys."""

from __future__ import annotations

import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.llm.prompt_cache_request_boundary import sanitize_prompt_cache_router_deployments
from src.llm.provider_cache import (
    PROVIDER_CACHE_REGISTRY,
    ProviderCacheRouteContext,
    apply_prompt_cache_hints,
)
from src.llm.usage import build_domain_hmac


def test_router_deployments_remove_only_configured_cache_key_without_mutation():
    original = [
        {
            "model_name": "openai/test-model",
            "litellm_params": {
                "model": "openai/test-model",
                "api_key": "sk-synthetic",
                "prompt_cache_key": "configured-key",
                "extra_headers": {"x-test": "preserved"},
            },
            "model_info": {"dsa_api_surface": "chat_completions"},
        },
        {"model_name": "openai/other-model", "litellm_params": {"model": "openai/other-model"}},
    ]
    before = copy.deepcopy(original)

    sanitized = sanitize_prompt_cache_router_deployments(original)

    assert original == before
    assert sanitized[0]["litellm_params"] == {
        "model": "openai/test-model",
        "api_key": "sk-synthetic",
        "extra_headers": {"x-test": "preserved"},
    }
    assert sanitized[0]["model_info"] == original[0]["model_info"]
    assert sanitized[1] == original[1]
    assert sanitized[0] is not original[0]
    assert sanitized[0]["litellm_params"]["extra_headers"] is not original[0]["litellm_params"]["extra_headers"]


@pytest.mark.parametrize(
    ("hints_enabled", "expected_reason"),
    [(False, "hints_disabled"), (True, "capability_not_verified")],
)
def test_unverified_request_key_is_removed(hints_enabled, expected_reason):
    original = {
        "model": "openai/test-model",
        "messages": [{"role": "user", "content": "synthetic"}],
        "prompt_cache_key": "caller-supplied-key",
        "temperature": 0.3,
    }
    before = copy.deepcopy(original)

    result = apply_prompt_cache_hints(
        original,
        ProviderCacheRouteContext(
            model="openai/test-model", provider="openai", api_surface="chat_completions",
        ),
        SimpleNamespace(llm_prompt_cache_hints_enabled=hints_enabled),
    )

    assert result.disabled_reason == expected_reason
    assert "prompt_cache_key" not in result.call_kwargs
    assert result.call_kwargs["temperature"] == 0.3
    assert original == before


def test_verified_request_key_is_replaced_with_hmac(monkeypatch):
    monkeypatch.setenv("LLM_USAGE_HMAC_SECRET", "synthetic-cache-secret")
    caps = replace(PROVIDER_CACHE_REGISTRY[0], verification_status="verified")
    original = {
        "model": "openai/gpt-4o",
        "messages": [{"role": "user", "content": "synthetic"}],
        "prompt_cache_key": "caller-supplied-key",
    }
    before = copy.deepcopy(original)
    route_context = ProviderCacheRouteContext(
        model="openai/gpt-4o", provider="openai", api_surface="chat_completions",
    )

    with patch("src.llm.provider_cache.resolve_provider_cache_caps", return_value=caps):
        result = apply_prompt_cache_hints(
            original,
            route_context,
            SimpleNamespace(llm_prompt_cache_hints_enabled=True),
        )

    assert result.hint_applied, result.disabled_reason
    assert result.call_kwargs["prompt_cache_key"] == build_domain_hmac(
        {
            "provider": caps.provider,
            "api_surface": caps.api_surface,
            "gateway": caps.gateway,
            "model_pattern": caps.model_pattern,
            "call_type": route_context.call_type,
        },
        domain="prompt_cache_key",
    )["hmac"]
    assert result.call_kwargs["prompt_cache_key"] != original["prompt_cache_key"]
    assert original == before


def test_verified_route_without_hmac_secret_drops_preconfigured_key():
    caps = replace(PROVIDER_CACHE_REGISTRY[0], verification_status="verified")
    original = {"model": "openai/gpt-4o", "prompt_cache_key": "caller-supplied-key"}

    with patch("src.llm.provider_cache.resolve_provider_cache_caps", return_value=caps), patch(
        "src.llm.provider_cache._safe_hmac_token", return_value=None,
    ):
        result = apply_prompt_cache_hints(
            original,
            ProviderCacheRouteContext(model="openai/gpt-4o", provider="openai"),
            SimpleNamespace(llm_prompt_cache_hints_enabled=True),
        )

    assert not result.hint_applied
    assert result.disabled_reason == "hmac_secret_unavailable"
    assert "prompt_cache_key" not in result.call_kwargs
    assert original["prompt_cache_key"] == "caller-supplied-key"


@pytest.mark.parametrize("legacy", [False, True])
def test_analyzer_router_receives_sanitized_copy_for_each_initialization(legacy):
    from src.analyzer import GeminiAnalyzer

    entries = [
        {
            "model_name": "__legacy_openai__" if legacy else "openai/test-model",
            "litellm_params": {
                "model": "__legacy_openai__" if legacy else "openai/test-model",
                "api_key": "sk-synthetic-first",
                "prompt_cache_key": "configured-key",
                "extra_headers": {"x-test": "preserved"},
            },
        }
    ]
    if legacy:
        second = copy.deepcopy(entries[0])
        second["litellm_params"]["api_key"] = "sk-synthetic-second"
        entries.append(second)
    before = copy.deepcopy(entries)
    config = SimpleNamespace(
        litellm_model="openai/test-model",
        llm_model_list=entries,
        openai_api_keys=["sk-synthetic-first", "sk-synthetic-second"] if legacy else [],
        gemini_api_keys=[],
        anthropic_api_keys=[],
        deepseek_api_keys=[],
        openai_base_url=None,
    )

    with patch("src.analyzer.Router") as router:
        GeminiAnalyzer(config=config)

    passed = router.call_args.kwargs["model_list"]
    assert entries == before
    assert len(passed) == len(entries)
    assert all("prompt_cache_key" not in item["litellm_params"] for item in passed)
    assert all(item["litellm_params"]["extra_headers"] == {"x-test": "preserved"} for item in passed)
