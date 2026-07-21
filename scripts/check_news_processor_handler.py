from __future__ import annotations

import asyncio
import json
from typing import Any

from router.schemas import (
    RouterInvokeRequest,
    RouterInvokeResponse,
)
from router.services.news_processor_handler import (
    NEWS_PROCESSOR_SYSTEM_PROMPT,
    NewsProcessorHandler,
)


VALID_OUTPUT = json.dumps(
    {
        "items": [
            {
                "event_time": None,
                "subjects": [
                    "测试公司",
                ],
                "event": "测试公司发布一项业务信息。",
                "source_class": "MEDIA_STATEMENT",
                "source_name": "测试媒体",
                "facts": [
                    {
                        "name": "测试数值",
                        "value": "100",
                        "unit": "万元",
                    }
                ],
                "sentiment": {
                    "direction": "neutral",
                    "strength": 0.2,
                    "evidence": [
                        "测试公司发布一项业务信息。",
                    ],
                },
                "market_signals": [],
                "confidence": 0.8,
            }
        ],
        "clusters": [
            {
                "topic": "测试事件",
                "item_indexes": [
                    0,
                ],
                "duplicate_level": "none",
                "incremental_information": [],
            }
        ],
        "summary": {
            "main_events": [
                "测试公司发布一项业务信息。",
            ],
            "dominant_sentiment": "neutral",
            "unverified_claims": [],
        },
    },
    ensure_ascii=False,
)


class FakeInvoker:
    def __init__(
        self,
        *,
        responses: list[RouterInvokeResponse],
        call_ids: list[str],
    ) -> None:
        self.responses = responses
        self.call_ids = call_ids
        self.calls: list[dict[str, Any]] = []

    async def __call__(
        self,
        *,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[RouterInvokeResponse, str]:
        index = len(self.calls)

        if index >= len(self.responses):
            raise RuntimeError(
                "FakeInvoker 没有更多测试响应"
            )

        self.calls.append(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
        )

        return (
            self.responses[index],
            self.call_ids[index],
        )


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def response(
    *,
    content: str,
    latency_ms: int,
    usage: dict[str, int],
) -> RouterInvokeResponse:
    return RouterInvokeResponse(
        role="news_processor",
        provider="longcat",
        model="LongCat-2.0",
        content=content,
        latency_ms=latency_ms,
        finish_reason="stop",
        usage=usage,
        validated=False,
        attempts=1,
    )


async def check_single_attempt() -> None:
    invoker = FakeInvoker(
        responses=[
            response(
                content=VALID_OUTPUT,
                latency_ms=120,
                usage={
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                    "total_tokens": 150,
                },
            )
        ],
        call_ids=[
            "call_single",
        ],
    )

    request = RouterInvokeRequest(
        role="news_processor",
        symbol="600172.SH",
        prompt="处理测试新闻。",
        temperature=0.2,
        max_tokens=512,
    )

    result = await NewsProcessorHandler().run(
        request=request,
        task_id="task_single",
        invoke_model=invoker,
    )

    print("Single-attempt handler:")
    print(
        f"validated={result.response.validated}"
    )
    print(
        f"attempts={result.response.attempts}"
    )
    print(
        f"task_id={result.response.task_id}"
    )
    print(
        f"call_ids={result.response.call_ids}"
    )
    print(f"confidence={result.confidence}")

    require(
        result.response.validated is True,
        "单次调用结果没有通过校验",
    )
    require(
        result.response.attempts == 1,
        "单次调用 attempts 错误",
    )
    require(
        result.response.task_id == "task_single",
        "单次调用 task_id 错误",
    )
    require(
        result.response.call_ids
        == ["call_single"],
        "单次调用 call_ids 错误",
    )
    require(
        result.confidence == 0.8,
        "平均置信度计算错误",
    )
    require(
        invoker.calls[0]["system_prompt"]
        == NEWS_PROCESSOR_SYSTEM_PROMPT,
        "没有使用默认新闻系统提示词",
    )


async def check_repair_attempt() -> None:
    invoker = FakeInvoker(
        responses=[
            response(
                content="not-json",
                latency_ms=100,
                usage={
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            ),
            response(
                content=VALID_OUTPUT,
                latency_ms=200,
                usage={
                    "prompt_tokens": 30,
                    "completion_tokens": 20,
                    "total_tokens": 50,
                },
            ),
        ],
        call_ids=[
            "call_first",
            "call_repair",
        ],
    )

    request = RouterInvokeRequest(
        role="news_processor",
        symbol="600172.SH",
        prompt="处理需要修复的测试新闻。",
        temperature=0.4,
        max_tokens=512,
    )

    result = await NewsProcessorHandler().run(
        request=request,
        task_id="task_repair",
        invoke_model=invoker,
    )

    print("\nRepair-attempt handler:")
    print(
        f"validated={result.response.validated}"
    )
    print(
        f"attempts={result.response.attempts}"
    )
    print(
        f"latency_ms={result.response.latency_ms}"
    )
    print(
        f"usage={result.response.usage}"
    )
    print(
        f"call_ids={result.response.call_ids}"
    )
    print(
        "repair_temperature="
        f"{invoker.calls[1]['temperature']}"
    )
    print(
        "repair_max_tokens="
        f"{invoker.calls[1]['max_tokens']}"
    )

    require(
        result.response.validated is True,
        "修复结果没有通过校验",
    )
    require(
        result.response.attempts == 2,
        "修复调用 attempts 错误",
    )
    require(
        result.response.latency_ms == 300,
        "修复调用耗时没有正确合并",
    )
    require(
        result.response.call_ids
        == [
            "call_first",
            "call_repair",
        ],
        "修复调用 call_ids 错误",
    )
    require(
        result.response.usage
        == {
            "prompt_tokens": 40,
            "completion_tokens": 25,
            "total_tokens": 65,
        },
        "修复调用 Token 用量没有正确合并",
    )
    require(
        invoker.calls[1]["temperature"] == 0,
        "修复调用 temperature 不是 0",
    )
    require(
        invoker.calls[1]["max_tokens"] == 4096,
        "修复调用 max_tokens 错误",
    )
    require(
        len(invoker.calls) == 2,
        "修复测试物理调用次数错误",
    )


async def check_handler() -> None:
    await check_single_attempt()
    await check_repair_attempt()

    print(
        "\nNews processor handler checks passed"
    )


def main() -> None:
    asyncio.run(check_handler())


if __name__ == "__main__":
    main()