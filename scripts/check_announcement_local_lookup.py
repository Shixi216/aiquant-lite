from __future__ import annotations

import httpx


DATA_HUB_URL = "http://127.0.0.1:8766"
SYMBOL = "600172.SH"
ANNOUNCEMENT_ID = "1225422790"


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    with httpx.Client(
        base_url=DATA_HUB_URL,
        timeout=30,
        trust_env=False,
    ) as client:
        by_announcement = client.get(
            f"/v1/stocks/{SYMBOL}/"
            "announcements/resolve",
            params={
                "announcement_id": ANNOUNCEMENT_ID,
            },
        )

        print("Lookup by announcement_id:")
        print(
            f"status_code={by_announcement.status_code}"
        )

        if by_announcement.status_code != 200:
            print(
                "response="
                f"{by_announcement.text[:1000]}"
            )
            by_announcement.raise_for_status()

        first = by_announcement.json()
        record_id = str(
            first.get("record_id") or ""
        )

        by_record = client.get(
            f"/v1/stocks/{SYMBOL}/"
            "announcements/resolve",
            params={
                "record_id": record_id,
            },
        )
        by_record.raise_for_status()
        second = by_record.json()

        by_both = client.get(
            f"/v1/stocks/{SYMBOL}/"
            "announcements/resolve",
            params={
                "record_id": record_id,
                "announcement_id": (
                    ANNOUNCEMENT_ID
                ),
            },
        )
        by_both.raise_for_status()
        third = by_both.json()

        missing_selector = client.get(
            f"/v1/stocks/{SYMBOL}/"
            "announcements/resolve"
        )

        mismatched = client.get(
            f"/v1/stocks/{SYMBOL}/"
            "announcements/resolve",
            params={
                "record_id": record_id,
                "announcement_id": "9999999999",
            },
        )

    source_url = str(
        first.get("source_url") or ""
    )

    print(f"record_id={record_id}")
    print(f"symbol={first.get('symbol')}")
    print(f"verified={first.get('verified')}")
    print(
        "source_level="
        f"{first.get('source_level')}"
    )
    print(f"source_url={source_url}")
    print(
        "record_lookup_status="
        f"{by_record.status_code}"
    )
    print(
        "combined_lookup_status="
        f"{by_both.status_code}"
    )
    print(
        "missing_selector_status="
        f"{missing_selector.status_code}"
    )
    print(
        "mismatched_selector_status="
        f"{mismatched.status_code}"
    )

    require(
        bool(record_id),
        "公告查询没有返回 record_id",
    )
    require(
        first.get("verified") is True,
        "查询结果不是已核验公告",
    )
    require(
        first.get("source_level") == "official",
        "查询结果不是官方来源",
    )
    require(
        ANNOUNCEMENT_ID in source_url,
        "查询结果的 announcement_id 错误",
    )
    require(
        second.get("record_id") == record_id,
        "record_id 查询返回了不同记录",
    )
    require(
        third.get("record_id") == record_id,
        "组合选择器返回了不同记录",
    )
    require(
        missing_selector.status_code == 422,
        "缺少选择器时没有返回 422",
    )
    require(
        mismatched.status_code == 404,
        "冲突选择器没有返回 404",
    )

    print(
        "\nAnnouncement local lookup "
        "checks passed"
    )


if __name__ == "__main__":
    main()