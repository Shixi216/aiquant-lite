from __future__ import annotations

from typing import Any

import httpx

from router.config import RouterSettings


def main() -> None:
    settings = RouterSettings()

    secret = settings.qwen_api_key
    api_key = (
        secret.get_secret_value().strip()
        if secret is not None
        else ""
    )
    base_url = (
        settings.qwen_base_url.strip().rstrip("/")
        if settings.qwen_base_url
        else ""
    )

    print("Qwen non-sensitive configuration check:")
    print(f"configured: {settings.qwen_ready}")
    print(f"base_url_configured: {bool(base_url)}")
    print(f"model: {settings.qwen_model}")
    print(f"key_length: {len(api_key)}")
    print(
        "starts_with_bearer: "
        f"{api_key.lower().startswith('bearer ')}"
    )
    print(
        "contains_whitespace: "
        f"{any(char.isspace() for char in api_key)}"
    )

    if not settings.qwen_ready:
        raise RuntimeError(
            "Qwen Router 配置不完整"
        )

    url = base_url + "/chat/completions"

    payload = {
        "model": settings.qwen_model,
        "messages": [
            {
                "role": "user",
                "content": (
                    "这是接口认证测试。"
                    "请只回复 AUTH_OK。"
                ),
            }
        ],
        "temperature": 0.1,
        "max_tokens": 32,
        "enable_thinking": False,
    }

    try:
        with httpx.Client(
            timeout=httpx.Timeout(
                timeout=90,
                connect=20,
            ),
            trust_env=False,
        ) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": (
                        f"Bearer {api_key}"
                    ),
                    "Content-Type": "application/json",
                },
                json=payload,
            )

    except httpx.HTTPError as exc:
        print("\nDirect authentication test:")
        print(
            "network_error: "
            f"{type(exc).__name__}"
        )
        print("result: network_error")
        return

    print("\nDirect authentication test:")
    print(f"status_code: {response.status_code}")

    if response.status_code in {401, 403}:
        print("result: credential_rejected")
        return

    if response.status_code == 404:
        print(
            "result: endpoint_or_model_not_found"
        )
        return

    if response.status_code != 200:
        print("result: request_rejected")
        return

    payload_json: dict[str, Any] = response.json()
    choices = payload_json.get("choices") or []

    if not choices:
        print("result: empty_choices")
        return

    first_choice = choices[0]
    message = first_choice.get("message") or {}
    content = str(
        message.get("content") or ""
    ).strip()

    usage = payload_json.get("usage") or {}

    print("result: authentication_passed")
    print(
        "returned_model: "
        f"{payload_json.get('model')}"
    )
    print(
        "finish_reason: "
        f"{first_choice.get('finish_reason')}"
    )
    print(f"content: {content[:100]}")
    print(
        "prompt_tokens: "
        f"{usage.get('prompt_tokens')}"
    )
    print(
        "completion_tokens: "
        f"{usage.get('completion_tokens')}"
    )


if __name__ == "__main__":
    main()