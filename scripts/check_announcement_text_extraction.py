from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import httpx

from router.services.announcement_document import (
    AnnouncementDocumentService,
)
from router.services.announcement_text import (
    AnnouncementTextService,
)


DATA_HUB_URL = "http://127.0.0.1:8766"
SYMBOL = "600172.SH"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def latest_announcement_url() -> str:
    today = date.today()
    start_date = date(
        today.year,
        1,
        1,
    ).isoformat()

    async with httpx.AsyncClient(
        base_url=DATA_HUB_URL,
        timeout=180,
        trust_env=False,
    ) as client:
        response = await client.get(
            f"/v1/stocks/{SYMBOL}/announcements",
            params={
                "start_date": start_date,
                "end_date": today.isoformat(),
            },
        )

    response.raise_for_status()
    payload = response.json()
    records = payload.get("records")

    require(
        isinstance(records, list) and bool(records),
        "Data Hub 没有返回公告",
    )

    record = records[0]
    data = record.get("data")

    if not isinstance(data, dict):
        data = {}

    source_url = str(
        data.get("url")
        or record.get("source_url")
        or ""
    ).strip()

    require(
        bool(source_url),
        "最新公告没有详情 URL",
    )

    return source_url


async def check_extraction() -> None:
    source_url = await latest_announcement_url()

    document = (
        await AnnouncementDocumentService().fetch(
            source_url
        )
    )

    service = AnnouncementTextService()

    first = service.extract(document)
    second = service.extract(document)

    text_path = Path(first.text_path)
    metadata_path = Path(first.metadata_path)

    cjk_count = sum(
        "\u4e00" <= character <= "\u9fff"
        for character in first.text
    )

    sample = first.text[:1200]

    print("Announcement text extraction:")
    print(f"announcement_id={first.announcement_id}")
    print(f"page_count={first.page_count}")
    print(
        "page_char_counts="
        f"{first.page_char_counts}"
    )
    print(f"text_length={first.text_length}")
    print(f"cjk_count={cjk_count}")
    print(f"requires_ocr={first.requires_ocr}")
    print(f"first_cache_hit={first.cache_hit}")
    print(f"second_cache_hit={second.cache_hit}")
    print(f"text_sha256={first.text_sha256}")
    print(f"text_path={first.text_path}")
    print(f"metadata_path={first.metadata_path}")

    print("\nText sample:")
    print(sample)

    require(
        first.page_count > 0,
        "公告 PDF 没有有效页面",
    )
    require(
        first.text_length >= 200,
        "提取到的文本过短",
    )
    require(
        cjk_count >= 20,
        "未提取到足够的中文文本",
    )
    require(
        first.requires_ocr is False,
        "当前公告被判定为需要 OCR",
    )
    require(
        text_path.exists(),
        "文本缓存文件不存在",
    )
    require(
        metadata_path.exists(),
        "文本元数据文件不存在",
    )
    require(
        second.cache_hit is True,
        "第二次提取没有命中缓存",
    )
    require(
        first.text_sha256 == second.text_sha256,
        "缓存前后文本哈希不一致",
    )
    require(
        first.text == second.text,
        "缓存前后文本内容不一致",
    )

    print(
        "\nAnnouncement text extraction "
        "checks passed"
    )


def main() -> None:
    asyncio.run(check_extraction())


if __name__ == "__main__":
    main()