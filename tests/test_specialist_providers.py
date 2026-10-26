from __future__ import annotations

from typing import Any

import pytest
from pydantic import SecretStr

from router.config import RouterSettings, router_settings
from router.providers.deepseek import DeepSeekProvider
from router.providers.mimo import MiMoProvider
from router.schemas import RouterInvokeResponse
from router.services.provider_registry import list_provider_names
from router.services.risk_controller_handler import normalize_risk_review_output


def test_provider_registry_excludes_retired_mimo():
    assert set(list_provider_names()) == {"deepseek", "longcat", "qwen"}


def test_deepseek_defaults_to_current_v4_pro_endpoint():
    settings = RouterSettings(_env_file=None, DEEPSEEK_API_KEY="test-only")

    assert settings.deepseek_ready is True
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model == "deepseek-v4-pro"


def test_retired_mimo_stays_disabled_even_with_complete_legacy_configuration():
    without_endpoint = RouterSettings(_env_file=None, XIAOMI_API_KEY="test-only")
    with_endpoint = RouterSettings(
        _env_file=None,
        XIAOMI_API_KEY="test-only",
        OPC_MIMO_BASE_URL="https://mimo.example.invalid/v1",
    )

    assert without_endpoint.mimo_ready is False
    assert with_endpoint.mimo_ready is False


@pytest.mark.asyncio
async def test_deepseek_provider_enables_reasoning_with_sufficient_budget(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    async def fake_invoke(**payload: Any) -> RouterInvokeResponse:
        captured.update(payload)
        return RouterInvokeResponse(
            role=payload["role"],
            provider="deepseek",
            model="deepseek-v4-pro",
            content="ok",
            latency_ms=1,
        )

    monkeypatch.setattr(router_settings, "deepseek_api_key", SecretStr("test-only"))
    monkeypatch.setattr(router_settings, "deepseek_base_url", "https://api.deepseek.com")
    monkeypatch.setattr(router_settings, "deepseek_model", "deepseek-v4-pro")
    monkeypatch.setattr("router.providers.deepseek.invoke_openai_compatible_chat", fake_invoke)

    await DeepSeekProvider().invoke(
        role="risk_controller",
        prompt="review",
        system_prompt="system",
        temperature=0,
        max_tokens=4096,
    )

    assert captured["extra_body"] == {
        "thinking": {"type": "enabled"},
        "reasoning_effort": "high",
    }
    assert captured["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_retired_mimo_adapter_never_sends_a_request(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(router_settings, "mimo_api_key", SecretStr("test-only"))
    monkeypatch.setattr(router_settings, "mimo_base_url", "https://mimo.example.invalid/v1")
    with pytest.raises(RuntimeError, match="retired"):
        await MiMoProvider().invoke(
            role="vision_reader", prompt="ignored", system_prompt="ignored",
            temperature=0, max_tokens=256,
        )


def test_risk_controller_accepts_json_code_fence():
    normalized, output = normalize_risk_review_output(
        """```json
{"decision":"approve","assessed_risk_level":"high","findings":[],
"required_actions":["human review"],"confidence":0.8}
```"""
    )

    assert output.decision == "approve"
    assert output.confidence == 0.8
    assert normalized.startswith("{")
