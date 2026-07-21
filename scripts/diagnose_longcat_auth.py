from __future__ import annotations

import hashlib

import httpx

from router.config import RouterSettings


def main() -> None:
    settings = RouterSettings()

    secret = settings.longcat_api_key
    key = (
        secret.get_secret_value().strip()
        if secret is not None
        else ""
    )

    starts_with_bearer = key.lower().startswith("bearer ")
    contains_whitespace = any(
        character.isspace()
        for character in key
    )
    has_outer_quotes = (
        len(key) >= 2
        and key[0] in {'"', "'"}
        and key[-1] == key[0]
    )
    repeated_prefix = (
        len(key) >= 40
        and key.find(key[:20], 20) >= 0
    )

    print("LongCat non-sensitive configuration check:")
    print(f"configured: {bool(key)}")
    print(f"base_url: {settings.longcat_base_url}")
    print(f"model: {settings.longcat_model}")
    print(f"key_length: {len(key)}")
    print(f"starts_with_bearer: {starts_with_bearer}")
    print(f"contains_whitespace: {contains_whitespace}")
    print(f"has_outer_quotes: {has_outer_quotes}")
    print(f"repeated_prefix: {repeated_prefix}")

    if not key:
        print("sha256_fingerprint: none")
        raise RuntimeError("LongCat API Key is not configured")

    fingerprint = hashlib.sha256(
        key.encode("utf-8")
    ).hexdigest()[:12]

    print(f"sha256_fingerprint: {fingerprint}")

    url = (
        settings.longcat_base_url.rstrip("/")
        + "/chat/completions"
    )

    try:
        with httpx.Client(
            timeout=30,
            trust_env=False,
        ) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.longcat_model,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Reply with OK.",
                        }
                    ],
                    "temperature": 0,
                    "max_tokens": 8,
                },
            )

        print("\nDirect authentication test:")
        print(f"status_code: {response.status_code}")

        if response.status_code == 401:
            print("result: credential_rejected")
        elif response.status_code == 200:
            print("result: authentication_passed")
        else:
            print("result: non_authentication_error")

    except httpx.HTTPError as exc:
        print("\nDirect authentication test:")
        print(f"network_error: {type(exc).__name__}")
        print("result: network_error")


if __name__ == "__main__":
    main()