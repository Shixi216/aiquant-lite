from __future__ import annotations

from router.config import router_settings
from router.providers.openai_compatible import invoke_openai_compatible_chat
from router.schemas import RouterInvokeResponse


class MiMoProvider:
    """Configurable OpenAI-compatible MiMo provider."""

    provider_name = "mimo"

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> RouterInvokeResponse:
        if (
            not router_settings.mimo_ready
            or router_settings.mimo_api_key is None
            or router_settings.mimo_base_url is None
        ):
            raise RuntimeError("MiMo Provider is not configured")

        return await invoke_openai_compatible_chat(
            provider_name=self.provider_name,
            provider_label="MiMo",
            secret=router_settings.mimo_api_key,
            base_url=router_settings.mimo_base_url,
            model=router_settings.mimo_model,
            role=role,
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
