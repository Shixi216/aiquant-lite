from __future__ import annotations

import asyncio
from datetime import date

from router.services.announcement_evidence import (
    AnnouncementEvidenceService,
)
from router.services.announcement_verifier import (
    AnnouncementVerifierService,
)


SYMBOL = "600172.SH"

CLAIMS = [
    (
        "公司预计2026年半年度归属于母公司"
        "所有者的净利润为-22,000万元。"
    ),
    "本期业绩预告数据已经会计师审计。",
    (
        "该公告披露了公司2026年全年"
        "营业收入的具体金额。"
    ),
]

EXPECTED_VERDICTS = [
    "SUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def check_verifier() -> None:
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

    run = await AnnouncementVerifierService().verify(
        bundle=bundle,
        claims=CLAIMS,
        max_tokens=5000,
    )

    output = run.output
    final_response = run.responses[-1]

    print("Announcement verifier:")
    print(
        "announcement_id="
        f"{output.announcement_id}"
    )
    print(f"title={output.title}")
    print(
        "overall_verdict="
        f"{output.overall_verdict}"
    )
    print(f"attempts={run.attempts}")
    print(
        "total_latency_ms="
        f"{run.total_latency_ms}"
    )
    print(
        "provider="
        f"{final_response.provider}"
    )
    print(f"model={final_response.model}")
    print(
        "final_finish_reason="
        f"{final_response.finish_reason}"
    )
    print(
        "final_usage="
        f"{final_response.usage}"
    )

    print("\nVerified claims:")

    for claim in output.claims:
        print(
            f"- {claim.claim_id}: "
            f"{claim.verdict} / "
            f"{claim.temporal_nature}"
        )
        print(f"  claim={claim.claim}")
        print(
            f"  confidence={claim.confidence}"
        )
        print(f"  reason={claim.reason}")

        for citation in claim.evidence:
            print(
                "  citation="
                f"page {citation.page_number}: "
                f"{citation.quote}"
            )

    require(
        output.announcement_id
        == bundle.announcement_id,
        "公告编号不一致",
    )
    require(
        output.title == bundle.title,
        "公告标题不一致",
    )
    require(
        output.overall_verdict
        == "PARTIALLY_SUPPORTED",
        "总体结论不符合混合结论规则",
    )
    require(
        len(output.claims) == len(CLAIMS),
        "模型遗漏或增加了待核验陈述",
    )
    require(
        [
            claim.claim
            for claim in output.claims
        ]
        == CLAIMS,
        "模型改变了陈述文本或顺序",
    )
    require(
        [
            claim.verdict
            for claim in output.claims
        ]
        == EXPECTED_VERDICTS,
        "分项核验结论不符合公告证据",
    )
    require(
        output.claims[0].temporal_nature
        == "COMPANY_ESTIMATE",
        "业绩预计没有标记为公司预计",
    )
    require(
        bool(output.claims[0].evidence),
        "支持结论缺少证据",
    )
    require(
        bool(output.claims[1].evidence),
        "反驳结论缺少证据",
    )
    require(
        not output.claims[2].evidence,
        "证据不足结论不应提供引用",
    )
    require(
        run.attempts in {1, 2},
        "自动修复次数异常",
    )
    require(
        final_response.provider == "qwen",
        "公告核验没有使用 Qwen Provider",
    )
    require(
        final_response.model == "qwen3.7-plus",
        "公告核验模型错误",
    )

    print(
        "\nAnnouncement verifier checks passed"
    )


def main() -> None:
    asyncio.run(check_verifier())


if __name__ == "__main__":
    main()