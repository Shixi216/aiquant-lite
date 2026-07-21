from __future__ import annotations

from router.config import router_settings
from router.providers.openai_compatible import invoke_openai_compatible_chat
from router.schemas import RouterInvokeResponse


class DeepSeekProvider:
    """Official DeepSeek OpenAI-compatible chat-completions provider."""

    provider_name = "deepseek"

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> RouterInvokeResponse:
        if not router_settings.deepseek_ready or router_settings.deepseek_api_key is None:
            raise RuntimeError("DeepSeek Provider is not configured")

        return await invoke_openai_compatible_chat(
            provider_name=self.provider_name,
            provider_label="DeepSeek",
            secret=router_settings.deepseek_api_key,
            base_url=router_settings.deepseek_base_url,
            model=router_settings.deepseek_model,
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={
                "thinking": {"type": "enabled"},
                "reasoning_effort": "high",
            },
        )
