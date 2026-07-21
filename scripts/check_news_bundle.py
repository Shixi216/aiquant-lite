from __future__ import annotations

import asyncio
import json
from datetime import date

from router.services.news_bundle import (
    NewsBundleService,
    build_news_processor_prompt,
)


SYMBOL = "600172.SH"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def check_bundle() -> None:
    today = date.today()
    start_date = date(
        today.year,
        1,
        1,
    ).isoformat()

    bundle = await NewsBundleService().build(
        symbol=SYMBOL,
        start_date=start_date,
        end_date=today.isoformat(),
        finance_news_limit=5,
        announcement_limit=5,
    )

    print("News source bundle:")
    print(f"symbol={bundle.symbol}")
    print(
        "finance_news_count="
        f"{bundle.finance_news_count}"
    )
    print(
        "announcement_count="
        f"{bundle.announcement_count}"
    )
    print(f"total_records={len(bundle.records)}")

    require(
        bundle.finance_news_count > 0,
        "资料包中没有财经新闻",
    )
    require(
        bundle.announcement_count > 0,
        "资料包中没有公司公告",
    )

    finance_news = [
        record
        for record in bundle.records
        if record.data_type == "finance_news"
    ]
    announcements = [
        record
        for record in bundle.records
        if record.data_type == "announcement"
    ]

    require(
        all(
            record.metadata_class
            == "MEDIA_STATEMENT"
            for record in finance_news
        ),
        "媒体新闻的元数据分类不正确",
    )

    require(
        all(
            record.metadata_class == "FACT"
            for record in announcements
        ),
        "官方公告的元数据分类不正确",
    )

    require(
        all(
            record.verified is True
            for record in announcements
        ),
        "资料包中存在未核验公告",
    )

    prompt = build_news_processor_prompt(bundle)

    print(f"prompt_length={len(prompt)}")

    require(
        len(prompt) <= 50000,
        "资料包提示词超过 Router 输入限制",
    )

    print("\nSelected records:")

    for index, record in enumerate(bundle.records):
        print(
            f"{index}: "
            f"{record.data_type}, "
            f"{record.metadata_class}, "
            f"{record.event_time}, "
            f"{record.title}"
        )

    print("\nFirst normalized record:")
    print(
        json.dumps(
            bundle.records[0].model_dump(
                mode="json"
            ),
            ensure_ascii=False,
            indent=2,
        )
    )

    print("\nNews bundle checks passed")


def main() -> None:
    asyncio.run(check_bundle())


if __name__ == "__main__":
    main()