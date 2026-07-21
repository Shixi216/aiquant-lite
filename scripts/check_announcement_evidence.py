from __future__ import annotations

import asyncio
from datetime import date

from router.services.announcement_evidence import (
    AnnouncementEvidenceService,
    build_announcement_verifier_prompt,
)


SYMBOL = "600172.SH"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def check_evidence() -> None:
    today = date.today()
    start_date = date(
        today.year,
        1,
        1,
    ).isoformat()

    bundle = (
        await AnnouncementEvidenceService().build_latest(
            symbol=SYMBOL,
            start_date=start_date,
            end_date=today.isoformat(),
        )
    )

    prompt = build_announcement_verifier_prompt(
        bundle,
        "请核验该公告披露的核心事件、"
        "主要数值及其属于历史事实还是公司预计。",
    )

    print("Announcement evidence bundle:")
    print(f"symbol={bundle.symbol}")
    print(f"title={bundle.title}")
    print(
        "announcement_id="
        f"{bundle.announcement_id}"
    )
    print(
        "announcement_date="
        f"{bundle.announcement_date}"
    )
    print(
        "source_authenticity="
        f"{bundle.source_authenticity}"
    )
    print(f"verified={bundle.verified}")
    print(f"page_count={bundle.page_count}")
    print(f"text_length={bundle.text_length}")
    print(f"requires_ocr={bundle.requires_ocr}")
    print(f"pdf_sha256={bundle.pdf_sha256}")
    print(f"text_sha256={bundle.text_sha256}")
    print(f"prompt_length={len(prompt)}")

    print("\nEvidence pages:")

    for page in bundle.pages:
        print(
            f"- page={page.page_number}, "
            f"chars={page.char_count}"
        )

    print("\nFirst page sample:")
    print(bundle.pages[0].text[:800])

    require(
        bundle.symbol == SYMBOL,
        "证据包股票代码错误",
    )
    require(
        bundle.verified is True,
        "证据包不是已核验官方来源",
    )
    require(
        bundle.source_level == "official",
        "证据包来源等级错误",
    )
    require(
        bundle.source_authenticity
        == "verified_official",
        "证据包来源真实性状态错误",
    )
    require(
        bundle.requires_ocr is False,
        "公告仍需要 OCR",
    )
    require(
        len(bundle.pages) == bundle.page_count,
        "证据包分页数量错误",
    )
    require(
        all(
            page.char_count > 0
            for page in bundle.pages
        ),
        "证据包存在空白页面",
    )
    require(
        len(prompt) < 50000,
        "公告核验提示词超过 Router 限制",
    )
    require(
        bundle.announcement_id in prompt,
        "提示词中缺少公告编号",
    )

    print(
        "\nAnnouncement evidence checks passed"
    )


def main() -> None:
    asyncio.run(check_evidence())


if __name__ == "__main__":
    main()