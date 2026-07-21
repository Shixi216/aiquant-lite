from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from router.schemas import (
    RouterInvokeRequest,
    RouterInvokeResponse,
)


class ModelInvoker(Protocol):
    """Callback for one audited physical model call."""

    async def __call__(
        self,
        *,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[RouterInvokeResponse, str]:
        ...


@dataclass(frozen=True)
class RoleHandlerResult:
    """Validated logical result returned by a role handler."""

    response: RouterInvokeResponse
    result_payload: dict[str, Any]
    confidence: float | None


class RoleHandler(Protocol):
    """Common contract for a generic Router role handler."""

    async def run(
        self,
        *,
        request: RouterInvokeRequest,
        task_id: str,
        invoke_model: ModelInvoker,
    ) -> RoleHandlerResult:
        ...