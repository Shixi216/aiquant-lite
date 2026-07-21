from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx


BASE_URL = "http://127.0.0.1:8766"
SYMBOL = "600172.SH"

TODAY = date.today()
START_DATE = date(TODAY.year, 1, 1).isoformat()
END_DATE = TODAY.isoformat()


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

        if len(text) > 300:
            return text[:300] + "...<truncated>"

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


def print_schema(payload: dict[str, Any]) -> None:
    for key in sorted(payload):
        value = payload[key]

        if isinstance(value, list):
            print(
                f"- {key}: array "
                f"(count={len(value)})"
            )
        elif isinstance(value, dict):
            child_keys = ", ".join(
                sorted(str(item) for item in value)
            )

            print(
                f"- {key}: object "
                f"(keys=[{child_keys}])"
            )
        else:
            print(
                f"- {key}: {value_type(value)}"
            )


def main() -> None:
    endpoint = (
        f"/v1/stocks/{SYMBOL}/announcements"
    )

    params = {
        "start_date": START_DATE,
        "end_date": END_DATE,
    }

    with httpx.Client(
        base_url=BASE_URL,
        timeout=180,
        trust_env=False,
    ) as client:
        health_response = client.get("/health")

        print("Data Hub health:")
        print(
            f"status_code={health_response.status_code}"
        )

        if health_response.status_code != 200:
            raise RuntimeError(
                "Data Hub is not healthy"
            )

        response = client.get(
            endpoint,
            params=params,
        )

    print("\n=== announcements ===")
    print(f"endpoint={endpoint}")
    print(f"start_date={START_DATE}")
    print(f"end_date={END_DATE}")
    print(f"status_code={response.status_code}")

    if response.status_code != 200:
        print(
            "response_body="
            f"{response.text[:1500]}"
        )
        raise RuntimeError(
            "Announcements endpoint returned "
            f"HTTP {response.status_code}"
        )

    payload = response.json()

    print(
        f"top_level_type={value_type(payload)}"
    )

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Announcements response is not an object"
        )

    print("top_level_schema:")
    print_schema(payload)

    records = payload.get("records")

    if not isinstance(records, list):
        raise RuntimeError(
            "Announcements response has no records array"
        )

    print("record_list_path=$.records")
    print(f"record_count={len(records)}")

    if not records:
        print("first_record=none")
        print(
            "\nAnnouncements contract inspection "
            "passed with zero records"
        )
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

    print(
        "\nAnnouncements data contract "
        "inspection passed"
    )


if __name__ == "__main__":
    main()