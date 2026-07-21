from __future__ import annotations

import json
from typing import Any

import httpx


BASE_URL = "http://127.0.0.1:8766"
SYMBOL = "600172.SH"

ENDPOINTS = {
    "finance_news": (
        f"/v1/stocks/{SYMBOL}/finance-news"
    ),
    "announcements": (
        f"/v1/stocks/{SYMBOL}/announcements"
    ),
}

PREFERRED_RECORD_KEYS = (
    "records",
    "items",
    "data",
    "results",
    "news",
    "announcements",
)


def value_type(value: Any) -> str:
    if value is None:
        return "null"

    if isinstance(value, bool):
        return "boolean"

    if isinstance(value, int):
        return "integer"

    if isinstance(value, float):
        return "number"

    if isinstance(value, str):
        return "string"

    if isinstance(value, list):
        return "array"

    if isinstance(value, dict):
        return "object"

    return type(value).__name__


def shorten(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()

        if len(text) > 240:
            return text[:240] + "...<truncated>"

        return text

    if isinstance(value, list):
        return [
            shorten(item)
            for item in value[:5]
        ]

    if isinstance(value, dict):
        return {
            str(key): shorten(item)
            for key, item in value.items()
        }

    return value


def find_record_list(
    value: Any,
    path: str = "$",
) -> tuple[str, list[Any]] | None:
    if isinstance(value, dict):
        for key in PREFERRED_RECORD_KEYS:
            candidate = value.get(key)

            if isinstance(candidate, list):
                return f"{path}.{key}", candidate

        for key, candidate in value.items():
            result = find_record_list(
                candidate,
                f"{path}.{key}",
            )

            if result is not None:
                return result

    if isinstance(value, list):
        return path, value

    return None


def print_object_schema(
    payload: dict[str, Any],
) -> None:
    for key in sorted(payload):
        value = payload[key]

        if isinstance(value, list):
            print(
                f"- {key}: array"
                f" (count={len(value)})"
            )
        elif isinstance(value, dict):
            child_keys = ", ".join(
                sorted(str(item) for item in value)
            )

            print(
                f"- {key}: object"
                f" (keys=[{child_keys}])"
            )
        else:
            print(
                f"- {key}: {value_type(value)}"
            )


def inspect_endpoint(
    client: httpx.Client,
    name: str,
    endpoint: str,
) -> None:
    print(f"\n=== {name} ===")
    print(f"endpoint={endpoint}")

    response = client.get(endpoint)

    print(f"status_code={response.status_code}")

    if response.status_code != 200:
        print(
            "response_body="
            f"{response.text[:1000]}"
        )
        raise RuntimeError(
            f"{name} endpoint returned "
            f"HTTP {response.status_code}"
        )

    payload = response.json()

    print(f"top_level_type={value_type(payload)}")

    if isinstance(payload, dict):
        print("top_level_schema:")
        print_object_schema(payload)

    record_result = find_record_list(payload)

    if record_result is None:
        print("record_list_path=not_found")
        print("compact_payload:")
        print(
            json.dumps(
                shorten(payload),
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    record_path, records = record_result

    print(f"record_list_path={record_path}")
    print(f"record_count={len(records)}")

    if not records:
        print("first_record=none")
        return

    first_record = records[0]

    print(
        "first_record_type="
        f"{value_type(first_record)}"
    )

    if isinstance(first_record, dict):
        print("first_record_schema:")

        for key in sorted(first_record):
            print(
                f"- {key}: "
                f"{value_type(first_record[key])}"
            )

    print("first_record_sample:")
    print(
        json.dumps(
            shorten(first_record),
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    with httpx.Client(
        base_url=BASE_URL,
        timeout=180,
        trust_env=False,
    ) as client:
        health_response = client.get("/health")

        print("Data Hub health:")
        print(
            f"status_code="
            f"{health_response.status_code}"
        )

        if health_response.status_code != 200:
            raise RuntimeError(
                "Data Hub is not healthy"
            )

        for name, endpoint in ENDPOINTS.items():
            inspect_endpoint(
                client,
                name,
                endpoint,
            )

    print("\nNews data contract inspection passed")


if __name__ == "__main__":
    main()