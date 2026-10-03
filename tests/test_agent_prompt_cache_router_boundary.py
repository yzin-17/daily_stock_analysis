# -*- coding: utf-8 -*-
"""Agent Router deployment prompt-cache boundary tests."""

from types import SimpleNamespace
from unittest.mock import patch

from tests.litellm_stub import ensure_litellm_stub

ensure_litellm_stub()

from src.agent.llm_adapter import LLMToolAdapter


def _config(model_list):
    return SimpleNamespace(
        agent_generation_backend="auto",
        generation_backend="litellm",
        agent_litellm_model="",
        litellm_model="openai/gpt-4o-mini",
        litellm_fallback_models=[],
        llm_model_list=model_list,
        llm_temperature=0.2,
    )


def test_agent_channel_router_drops_configured_key_before_dispatch():
    deployment = {
        "model_name": "openai/gpt-4o-mini",
        "litellm_params": {
            "model": "openai/gpt-4o-mini",
            "api_key": "synthetic-key",
            "api_base": "http://127.0.0.1:8765/v1",
            "prompt_cache_key": "synthetic-cache-key",
        },
        "model_info": {"id": "synthetic-route"},
    }
    config = _config([deployment])
    captured = []

    class CapturingRouter:
        def __init__(self, *, model_list, **_kwargs):
            self.model_list = model_list

        def completion(self, **kwargs):
            captured.append({**self.model_list[0]["litellm_params"], **kwargs})
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=[]))],
            )

    with patch("src.agent.llm_adapter.Router", CapturingRouter), patch.object(
        LLMToolAdapter, "_register_custom_model_pricing"
    ):
        adapter = LLMToolAdapter(config)
        result = adapter.call_text([{"role": "user", "content": "synthetic prompt"}])

    assert result.content == "ok"
    assert len(captured) == 1
    assert "prompt_cache_key" not in captured[0]
    assert captured[0]["api_base"] == "http://127.0.0.1:8765/v1"
    assert adapter._router.model_list[0]["model_info"] == {"id": "synthetic-route"}
    assert adapter._router.model_list[0] is not deployment
    assert deployment["litellm_params"]["prompt_cache_key"] == "synthetic-cache-key"


def test_agent_legacy_router_sanitizes_all_deployments():
    model_lists = []

    class CapturingRouter:
        def __init__(self, *, model_list, **_kwargs):
            model_lists.append(model_list)

    with patch("src.agent.llm_adapter.Router", CapturingRouter), patch.object(
        LLMToolAdapter, "_register_custom_model_pricing"
    ), patch("src.agent.llm_adapter.get_api_keys_for_model", return_value=["key-a", "key-b"]), patch(
        "src.agent.llm_adapter.extra_litellm_params",
        return_value={"api_base": "http://127.0.0.1:8765/v1", "prompt_cache_key": "synthetic"},
    ):
        adapter = LLMToolAdapter(_config([]))

    assert adapter.is_available
    assert len(model_lists) == 1
    assert len(model_lists[0]) == 2
    assert all("prompt_cache_key" not in row["litellm_params"] for row in model_lists[0])
    assert all(row["litellm_params"]["api_base"] == "http://127.0.0.1:8765/v1" for row in model_lists[0])
    assert all(row["litellm_params"]["prompt_cache_key"] == "synthetic" for row in adapter._legacy_router_model_list)
