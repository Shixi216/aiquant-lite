from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx
from pydantic import SecretStr

from router.schemas import RouterInvokeResponse


async def invoke_openai_compatible_chat(
    *,
    provider_name: str,
    provider_label: str,
    secret: SecretStr,
    base_url: str,
    model: str,
    role: str,
    prompt: str,
    system_prompt: str,
    temperature: float,
    max_tokens: int,
    extra_body: dict[str, Any] | None = None,
) -> RouterInvokeResponse:
    """Invoke one non-streaming OpenAI-compatible chat-completions endpoint."""
    request_payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if extra_body:
        request_payload.update(extra_body)

    headers = {
        "Authorization": f"Bearer {secret.get_secret_value().strip()}",
        "Content-Type": "application/json",
    }
    url = base_url.rstrip("/") + "/chat/completions"
    started = perf_counter()

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout=180, connect=20),
            trust_env=False,
        ) as client:
            response = await client.post(url, headers=headers, json=request_payload)
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"{provider_label} API returned HTTP {exc.response.status_code}"
        ) from exc
    except httpx.HTTPError as exc:
        raise RuntimeError(
            f"{provider_label} API network error: {type(exc).__name__}: {exc}"
        ) from exc

    latency_ms = round((perf_counter() - started) * 1000)
    try:
        payload: dict[str, Any] = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{provider_label} API returned invalid JSON") from exc

    choices = payload.get("choices") or []
    if not choices or not isinstance(choices[0], dict):
        raise RuntimeError(f"{provider_label} API returned no choices")

    first_choice = choices[0]
    message = first_choice.get("message") or {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        normalized_content = content.strip()
    elif content is not None:
        normalized_content = json.dumps(content, ensure_ascii=False)
    else:
        normalized_content = ""
    if not normalized_content:
        raise RuntimeError(f"{provider_label} API returned empty content")

    usage_raw = payload.get("usage") or {}
    usage = {
        str(key): int(value)
        for key, value in usage_raw.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    }
    return RouterInvokeResponse(
        role=role,
        provider=provider_name,
        model=str(payload.get("model") or model),
        content=normalized_content,
        latency_ms=latency_ms,
        finish_reason=first_choice.get("finish_reason"),
        usage=usage,
    )
