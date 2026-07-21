from __future__ import annotations

import json

from pydantic import ValidationError

from router.schemas import NewsProcessorOutput


class NewsOutputValidationError(RuntimeError):
    """Raised when news processor output violates its schema."""


def _compact_validation_errors(
    exc: ValidationError,
) -> str:
    messages: list[str] = []

    for error in exc.errors(include_url=False):
        location = ".".join(
            str(part)
            for part in error.get("loc", ())
        )
        message = str(error.get("msg", "invalid value"))

        if location:
            messages.append(f"{location}: {message}")
        else:
            messages.append(message)

    return "; ".join(messages)[:2000]


def normalize_news_output(content: str) -> str:
    stripped = content.strip()

    if not stripped:
        raise NewsOutputValidationError(
            "模型返回内容为空"
        )

    if "```" in stripped:
        raise NewsOutputValidationError(
            "模型输出包含 Markdown 代码围栏"
        )

    try:
        parsed = NewsProcessorOutput.model_validate_json(
            stripped
        )
    except ValidationError as exc:
        details = _compact_validation_errors(exc)

        raise NewsOutputValidationError(
            f"新闻 JSON Schema 校验失败：{details}"
        ) from exc

    return json.dumps(
        parsed.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_news_repair_prompt(
    original_prompt: str,
    invalid_output: str,
    validation_error: str,
) -> str:
    safe_prompt = original_prompt[:20000]
    safe_output = invalid_output[:12000]
    safe_error = validation_error[:2000]

    return f"""
上一轮输出未通过系统的 JSON Schema 校验。请根据错误信息修正输出。

硬性要求：
1. 只返回一个合法 JSON 对象；
2. 不得使用 Markdown 或代码围栏；
3. 不得添加解释、前言或免责声明；
4. 必须严格符合系统消息中规定的字段结构；
5. 不得改变原始材料的事实等级；
6. 不得编造缺失信息。

校验错误：
{safe_error}

原始任务：
{safe_prompt}

上一轮无效输出：
{safe_output}
""".strip()