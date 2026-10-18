from __future__ import annotations

from router.schemas import RouterInvokeResponse


class MiMoProvider:
    """Retired compatibility adapter; configuration can no longer enable it."""

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
        raise RuntimeError("MiMo Provider has been retired")
