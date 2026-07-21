from __future__ import annotations

from typing import Protocol

from router.schemas import RouterInvokeResponse


class RouterModelProvider(Protocol):
    """Common contract for Router model providers."""

    provider_name: str

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> RouterInvokeResponse:
        ...