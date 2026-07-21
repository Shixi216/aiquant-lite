from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from router.schemas import (
    AnnouncementEvidenceBundle,
    AnnouncementVerificationOutput,
)


CODE_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$",
    re.IGNORECASE | re.DOTALL,
)


def normalize_quote(value: str) -> str:
    return re.sub(
        r"\s+",
        "",
        value,
    )


def decode_json_object(
    raw_content: str,
) -> dict[str, Any]:
    content = raw_content.strip()
    match = CODE_FENCE_PATTERN.fullmatch(content)

    if match is not None:
        content = match.group("body").strip()

    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "公告核验结果不是合法 JSON："
            f"line={exc.lineno}, column={exc.colno}"
        ) from exc

    if not isinstance(payload, dict):
        raise ValueError(
            "公告核验结果顶层必须是 JSON 对象"
        )

    return payload


def expected_overall_verdict(
    output: AnnouncementVerificationOutput,
) -> str:
    verdicts = {
        claim.verdict
        for claim in output.claims
    }

    if len(verdicts) == 1:
        return next(iter(verdicts))

    return "PARTIALLY_SUPPORTED"


def validate_claim_evidence(
    output: AnnouncementVerificationOutput,
    bundle: AnnouncementEvidenceBundle,
) -> None:
    page_map = {
        page.page_number: page.text
        for page in bundle.pages
    }

    seen_claim_ids: set[str] = set()

    for claim in output.claims:
        if claim.claim_id in seen_claim_ids:
            raise ValueError(
                "公告核验结果存在重复 claim_id："
                f"{claim.claim_id}"
            )

        seen_claim_ids.add(claim.claim_id)

        if (
            claim.verdict
            != "INSUFFICIENT_EVIDENCE"
            and not claim.evidence
        ):
            raise ValueError(
                f"{claim.claim_id} 的结论为 "
                f"{claim.verdict}，但没有证据引用"
            )

        if (
            claim.verdict
            == "INSUFFICIENT_EVIDENCE"
            and claim.evidence
        ):
            raise ValueError(
                f"{claim.claim_id} 被标记为证据不足，"
                "但仍提供了确定性证据引用"
            )

        for citation in claim.evidence:
            page_text = page_map.get(
                citation.page_number
            )

            if page_text is None:
                raise ValueError(
                    f"{claim.claim_id} 引用了不存在的页码："
                    f"{citation.page_number}"
                )

            normalized_page = normalize_quote(
                page_text
            )
            normalized_citation = normalize_quote(
                citation.quote
            )

            if normalized_citation not in normalized_page:
                raise ValueError(
                    f"{claim.claim_id} 的引用原文未在"
                    f"第 {citation.page_number} 页找到"
                )


def validate_announcement_verification(
    raw_content: str,
    bundle: AnnouncementEvidenceBundle,
) -> AnnouncementVerificationOutput:
    if (
        not bundle.verified
        or bundle.source_level != "official"
        or bundle.source_authenticity
        != "verified_official"
    ):
        raise ValueError(
            "公告证据包没有通过官方来源校验"
        )

    if bundle.requires_ocr:
        raise ValueError(
            "公告证据包仍需要 OCR，不能直接核验"
        )

    payload = decode_json_object(raw_content)

    try:
        output = (
            AnnouncementVerificationOutput
            .model_validate(payload)
        )
    except ValidationError as exc:
        raise ValueError(
            "公告核验结果未通过 JSON Schema："
            f"{exc}"
        ) from exc

    if (
        output.announcement_id
        != bundle.announcement_id
    ):
        raise ValueError(
            "核验结果中的公告编号与证据包不一致"
        )

    if output.title.strip() != bundle.title.strip():
        raise ValueError(
            "核验结果中的公告标题与证据包不一致"
        )

    validate_claim_evidence(
        output,
        bundle,
    )

    expected = expected_overall_verdict(output)

    if output.overall_verdict != expected:
        raise ValueError(
            "总体结论与分项结论不一致："
            f"expected={expected}, "
            f"actual={output.overall_verdict}"
        )

    return output