from __future__ import annotations

import asyncio
import copy
import json
from datetime import date
from typing import Any

from router.services.announcement_evidence import (
    AnnouncementEvidenceService,
)
from router.services.announcement_verification import (
    validate_announcement_verification,
)


SYMBOL = "600172.SH"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def expect_rejected(
    payload: dict[str, Any],
    bundle: Any,
    test_name: str,
) -> None:
    try:
        validate_announcement_verification(
            json.dumps(
                payload,
                ensure_ascii=False,
            ),
            bundle,
        )
    except ValueError as exc:
        print(
            f"rejected_{test_name}=True "
            f"reason={str(exc)[:140]}"
        )
        return

    raise RuntimeError(
        f"反向测试未被拒绝：{test_name}"
    )


async def check_schema() -> None:
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

    first_page = bundle.pages[0]
    first_line = next(
        line.strip()
        for line in first_page.text.splitlines()
        if line.strip()
    )

    valid_payload: dict[str, Any] = {
        "announcement_id": bundle.announcement_id,
        "title": bundle.title,
        "overall_verdict": (
            "PARTIALLY_SUPPORTED"
        ),
        "summary": (
            "公告证券代码能够由正文支持；"
            "公告未确认公司全年已经盈利。"
        ),
        "claims": [
            {
                "claim_id": "claim_1",
                "claim": (
                    "该公告对应证券代码600172。"
                ),
                "verdict": "SUPPORTED",
                "temporal_nature": (
                    "CURRENT_STATUS"
                ),
                "normalized_fact": (
                    "证券代码为600172。"
                ),
                "evidence": [
                    {
                        "page_number": (
                            first_page.page_number
                        ),
                        "quote": first_line,
                    }
                ],
                "reason": (
                    "公告首页明确列示证券代码。"
                ),
                "confidence": 1.0,
            },
            {
                "claim_id": "claim_2",
                "claim": (
                    "公告确认公司2026年全年"
                    "已经实现盈利。"
                ),
                "verdict": (
                    "INSUFFICIENT_EVIDENCE"
                ),
                "temporal_nature": "UNKNOWN",
                "normalized_fact": None,
                "evidence": [],
                "reason": (
                    "公告属于半年度业绩预告，"
                    "没有确认全年已经实现盈利。"
                ),
                "confidence": 0.98,
            },
        ],
        "warnings": [
            "业绩预告数据未经会计师审计。"
        ],
    }

    raw_content = json.dumps(
        valid_payload,
        ensure_ascii=False,
    )

    output = validate_announcement_verification(
        raw_content,
        bundle,
    )

    fenced_output = (
        "```json\n"
        + raw_content
        + "\n```"
    )

    fenced = validate_announcement_verification(
        fenced_output,
        bundle,
    )

    print("Announcement verification schema:")
    print(
        "announcement_id="
        f"{output.announcement_id}"
    )
    print(f"title={output.title}")
    print(
        "overall_verdict="
        f"{output.overall_verdict}"
    )
    print(f"claim_count={len(output.claims)}")
    print(
        "fenced_json_accepted="
        f"{fenced.announcement_id == output.announcement_id}"
    )
    print(
        "citation_page="
        f"{output.claims[0].evidence[0].page_number}"
    )
    print(
        "citation_quote="
        f"{output.claims[0].evidence[0].quote}"
    )

    require(
        output.overall_verdict
        == "PARTIALLY_SUPPORTED",
        "总体结论错误",
    )
    require(
        len(output.claims) == 2,
        "分项结论数量错误",
    )

    bad_quote = copy.deepcopy(valid_payload)
    bad_quote["claims"][0]["evidence"][0][
        "quote"
    ] = "这是一段公告中不存在的伪造引用。"

    expect_rejected(
        bad_quote,
        bundle,
        "invented_quote",
    )

    bad_page = copy.deepcopy(valid_payload)
    bad_page["claims"][0]["evidence"][0][
        "page_number"
    ] = 999

    expect_rejected(
        bad_page,
        bundle,
        "invalid_page",
    )

    missing_evidence = copy.deepcopy(valid_payload)
    missing_evidence["claims"][0]["evidence"] = []

    expect_rejected(
        missing_evidence,
        bundle,
        "missing_evidence",
    )

    mismatched_id = copy.deepcopy(valid_payload)
    mismatched_id["announcement_id"] = (
        "9999999999"
    )

    expect_rejected(
        mismatched_id,
        bundle,
        "mismatched_announcement_id",
    )

    duplicate_claim = copy.deepcopy(valid_payload)
    duplicate_claim["claims"][1]["claim_id"] = (
        "claim_1"
    )

    expect_rejected(
        duplicate_claim,
        bundle,
        "duplicate_claim_id",
    )

    print(
        "\nAnnouncement verification schema "
        "checks passed"
    )


def main() -> None:
    asyncio.run(check_schema())


if __name__ == "__main__":
    main()