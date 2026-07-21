from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx

from router.config import router_settings
from router.schemas import RouterInvokeResponse


class QwenProvider:
    """OpenAI-compatible Qwen chat-completions provider."""

    provider_name = "qwen"

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        enable_thinking: bool = False,
    ) -> RouterInvokeResponse:
        if not router_settings.qwen_ready:
            raise RuntimeError(
                "Qwen Provider 配置不完整"
            )

        secret = router_settings.qwen_api_key
        base_url = router_settings.qwen_base_url

        if secret is None or base_url is None:
            raise RuntimeError(
                "Qwen Provider 配置不完整"
            )

        url = (
            base_url.rstrip("/")
            + "/chat/completions"
        )

        request_payload = {
            "model": router_settings.qwen_model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "enable_thinking": enable_thinking,
        }

        headers = {
            "Authorization": (
                "Bearer "
                + secret.get_secret_value().strip()
            ),
            "Content-Type": "application/json",
        }

        started = perf_counter()

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(
                    timeout=180,
                    connect=20,
                ),
                trust_env=False,
            ) as client:
                response = await client.post(
                    url,
                    headers=headers,
                    json=request_payload,
                )

            response.raise_for_status()

        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                "Qwen API 返回错误："
                f"HTTP {exc.response.status_code}"
            ) from exc

        except httpx.HTTPError as exc:
            raise RuntimeError(
                "Qwen API 网络错误："
                f"{type(exc).__name__}: {exc}"
            ) from exc

        latency_ms = round(
            (perf_counter() - started) * 1000
        )

        payload: dict[str, Any] = response.json()
        choices = payload.get("choices") or []

        if not choices:
            raise RuntimeError(
                "Qwen API 没有返回 choices"
            )

        first_choice = choices[0]
        message = first_choice.get("message") or {}
        content = message.get("content")

        if isinstance(content, str):
            normalized_content = content.strip()
        else:
            normalized_content = json.dumps(
                content,
                ensure_ascii=False,
            )

        if not normalized_content:
            raise RuntimeError(
                "Qwen API 返回内容为空"
            )

        usage_raw = payload.get("usage") or {}
        usage: dict[str, int] = {}

        for key, value in usage_raw.items():
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
            ):
                usage[str(key)] = int(value)

        return RouterInvokeResponse(
            role=role,
            provider=self.provider_name,
            model=str(
                payload.get("model")
                or router_settings.qwen_model
            ),
            content=normalized_content,
            latency_ms=latency_ms,
            finish_reason=first_choice.get(
                "finish_reason"
            ),
            usage=usage,
            validated=False,
            attempts=1,
        )