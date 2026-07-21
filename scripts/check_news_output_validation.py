from __future__ import annotations

import json

from router.services.news_output import (
    NewsOutputValidationError,
    normalize_news_output,
)


VALID_OUTPUT = {
    "items": [
        {
            "event_time": "2026-07-16T10:30:00+08:00",
            "subjects": ["黄河旋风", "某券商"],
            "event": "某券商上调黄河旋风评级",
            "source_class": "MEDIA_STATEMENT",
            "source_name": "测试财经媒体",
            "facts": [
                {
                    "name": "目标价",
                    "value": 15,
                    "unit": "元",
                }
            ],
            "sentiment": {
                "direction": "positive",
                "strength": 0.7,
                "evidence": ["评级上调"],
            },
            "market_signals": [
                {
                    "type": "target_price",
                    "value": "15元",
                    "attribution": "某券商",
                }
            ],
            "confidence": 0.5,
        }
    ],
    "clusters": [],
    "summary": {
        "main_events": ["评级上调"],
        "dominant_sentiment": "positive",
        "unverified_claims": ["尚未获得官方确认"],
    },
}


def expect_failure(
    content: str,
    test_name: str,
) -> None:
    try:
        normalize_news_output(content)
    except NewsOutputValidationError:
        print(f"{test_name}: rejected")
        return

    raise RuntimeError(
        f"{test_name}: invalid content was accepted"
    )


def main() -> None:
    valid_content = json.dumps(
        VALID_OUTPUT,
        ensure_ascii=False,
    )

    normalized = normalize_news_output(valid_content)
    parsed = json.loads(normalized)

    print("Valid output test:")
    print(f"source_class: {parsed['items'][0]['source_class']}")
    print("result: passed")

    invalid_confidence = json.loads(valid_content)
    invalid_confidence["items"][0]["confidence"] = 1.5

    expect_failure(
        json.dumps(
            invalid_confidence,
            ensure_ascii=False,
        ),
        "confidence range test",
    )

    invalid_source = json.loads(valid_content)
    invalid_source["items"][0]["source_class"] = "UNKNOWN"

    expect_failure(
        json.dumps(
            invalid_source,
            ensure_ascii=False,
        ),
        "source class test",
    )

    expect_failure(
        f"```json\n{valid_content}\n```",
        "markdown fence test",
    )

    print("\nNews output validation checks passed")


if __name__ == "__main__":
    main()