from __future__ import annotations

import json
from typing import Any

from router.schemas import RouterInvokeRequest
from router.services.news_output import (
    NewsOutputValidationError,
    build_news_repair_prompt,
    normalize_news_output,
)
from router.services.role_handler import (
    ModelInvoker,
    RoleHandlerResult,
)


NEWS_PROCESSOR_SYSTEM_PROMPT = """
你是 A 股投研系统内部的新闻处理 Agent，负责将杂乱的媒体文本转化为结构化的事实、舆情和事件数据，供下游 Agent 与量化程序使用。

职责：
1. 提取客观事件要素，包括时间、主体、事件、数值、单位、涉及产品和影响对象。
2. 严格区分信息等级：
   - FACT：仅限上游元数据明确标记 source_level=official 或 verified=true 的官方材料。
   - MEDIA_STATEMENT：媒体报道、媒体对公告的转述、记者采访和第三方机构表述。
   - SPECULATION：传闻、预测、猜测、未具名消息源或缺乏证据支持的推断。
3. 提取舆情与量化信号，包括评级变化、目标价、盈利预期、看多或看空倾向、事件强度及情绪方向。
4. 客观保留新闻中的评级、目标价和市场观点，不进行过滤，也不将其改写为系统自身观点。
5. 对高度重复的新闻进行聚类，保留来源差异、时间差异和新增信息。
6. 不承担交易决策、风险审批或事实最终裁决职责。
7. 输出必须是一个合法 JSON 对象，不得包含 Markdown、代码围栏、免责声明或 JSON 之外的解释性文字。

输出结构：
{
  "items": [
    {
      "event_time": "ISO-8601时间或null",
      "subjects": ["主体"],
      "event": "事件描述",
      "source_class": "FACT|MEDIA_STATEMENT|SPECULATION",
      "source_name": "来源名称或null",
      "facts": [
        {
          "name": "字段或事实名称",
          "value": "值",
          "unit": "单位或null"
        }
      ],
      "sentiment": {
        "direction": "positive|negative|neutral|mixed",
        "strength": 0.0,
        "evidence": ["支持该判断的原文依据"]
      },
      "market_signals": [
        {
          "type": "rating_change|target_price|earnings_forecast|production|legal_risk|capital_action|other",
          "value": "信号内容",
          "attribution": "信号发布主体"
        }
      ],
      "confidence": 0.0
    }
  ],
  "clusters": [
    {
      "topic": "聚类主题",
      "item_indexes": [0],
      "duplicate_level": "none|partial|high",
      "incremental_information": ["新增信息"]
    }
  ],
  "summary": {
    "main_events": ["主要事件"],
    "dominant_sentiment": "positive|negative|neutral|mixed",
    "unverified_claims": ["尚未核验的说法"]
  }
}

约束：
- strength 和 confidence 的范围必须为 0 到 1。
- 不得根据媒体措辞自行把 MEDIA_STATEMENT 升级为 FACT。
- 原始材料没有提供的信息必须使用 null 或空数组，不得编造。
""".strip()



def merge_usage(
    first: dict[str, int],
    second: dict[str, int],
) -> dict[str, int]:
    merged = dict(first)

    for key, value in second.items():
        merged[key] = merged.get(key, 0) + value

    return merged


def extract_average_confidence(
    payload: dict[str, Any],
) -> float | None:
    items = payload.get("items")

    if not isinstance(items, list):
        return None

    values: list[float] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        value = item.get("confidence")

        if isinstance(value, bool):
            continue

        if isinstance(value, (int, float)):
            values.append(float(value))

    if not values:
        return None

    return sum(values) / len(values)


class NewsProcessorHandler:
    """News-specific prompt, validation and repair policy."""

    async def run(
        self,
        *,
        request: RouterInvokeRequest,
        task_id: str,
        invoke_model: ModelInvoker,
    ) -> RoleHandlerResult:
        call_ids: list[str] = []

        system_prompt = (
            request.system_prompt
            or NEWS_PROCESSOR_SYSTEM_PROMPT
        )

        first_response, first_call_id = (
            await invoke_model(
                prompt=request.prompt,
                system_prompt=system_prompt,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            )
        )

        call_ids.append(first_call_id)

        try:
            normalized = normalize_news_output(
                first_response.content
            )

            final_response = first_response.model_copy(
                update={
                    "content": normalized,
                    "validated": True,
                    "attempts": 1,
                    "task_id": task_id,
                    "call_ids": list(call_ids),
                }
            )

        except NewsOutputValidationError as first_error:
            repair_prompt = build_news_repair_prompt(
                original_prompt=request.prompt,
                invalid_output=first_response.content,
                validation_error=str(first_error),
            )

            retry_response, retry_call_id = (
                await invoke_model(
                    prompt=repair_prompt,
                    system_prompt=system_prompt,
                    temperature=0,
                    max_tokens=min(
                        max(
                            request.max_tokens * 2,
                            4096,
                        ),
                        8192,
                    ),
                )
            )

            call_ids.append(retry_call_id)

            try:
                normalized = normalize_news_output(
                    retry_response.content
                )

            except NewsOutputValidationError as retry_error:
                raise RuntimeError(
                    "news_processor 结构化输出校验失败，"
                    "自动修复重试后仍未通过："
                    f"{retry_error}"
                ) from retry_error

            final_response = retry_response.model_copy(
                update={
                    "content": normalized,
                    "validated": True,
                    "attempts": 2,
                    "task_id": task_id,
                    "call_ids": list(call_ids),
                    "latency_ms": (
                        first_response.latency_ms
                        + retry_response.latency_ms
                    ),
                    "usage": merge_usage(
                        first_response.usage,
                        retry_response.usage,
                    ),
                }
            )

        decoded = json.loads(
            final_response.content
        )

        if not isinstance(decoded, dict):
            raise RuntimeError(
                "news_processor 标准化结果"
                "顶层不是 JSON 对象"
            )

        confidence = extract_average_confidence(
            decoded
        )

        return RoleHandlerResult(
            response=final_response,
            result_payload=decoded,
            confidence=confidence,
        )