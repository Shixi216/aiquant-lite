from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import httpx

from router.services.announcement_document import (
    AnnouncementDocumentService,
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


async def check_fetch() -> None:
    source_url = await latest_announcement_url()
    service = AnnouncementDocumentService()

    first = await service.fetch(source_url)
    second = await service.fetch(source_url)

    pdf_path = Path(first.local_path)
    metadata_path = Path(first.metadata_path)

    print("Announcement PDF fetch:")
    print(f"announcement_id={first.announcement_id}")
    print(
        "announcement_time="
        f"{first.announcement_time}"
    )
    print(f"pdf_url={first.pdf_url}")
    print(f"content_type={first.content_type}")
    print(f"size_bytes={first.size_bytes}")
    print(f"sha256={first.sha256}")
    print(f"first_cache_hit={first.cache_hit}")
    print(f"second_cache_hit={second.cache_hit}")
    print(f"local_path={first.local_path}")
    print(f"metadata_path={first.metadata_path}")

    require(
        pdf_path.exists(),
        "公告 PDF 没有写入缓存",
    )
    require(
        metadata_path.exists(),
        "公告 PDF 元数据没有写入缓存",
    )
    require(
        pdf_path.read_bytes()[:5] == b"%PDF-",
        "缓存文件不是合法 PDF",
    )
    require(
        first.size_bytes >= 1024,
        "公告 PDF 尺寸异常",
    )
    require(
        second.cache_hit is True,
        "第二次获取没有命中缓存",
    )
    require(
        first.sha256 == second.sha256,
        "缓存前后 SHA-256 不一致",
    )
    require(
        first.local_path == second.local_path,
        "缓存前后文件路径不一致",
    )

    print(
        "\nAnnouncement document fetch checks passed"
    )


def main() -> None:
    asyncio.run(check_fetch())


if __name__ == "__main__":
    main()