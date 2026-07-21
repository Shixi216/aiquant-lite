from __future__ import annotations

import asyncio

from router.config import RouterSettings
from router.providers import QwenProvider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def check_provider() -> None:
    settings = RouterSettings()

    print("Qwen Provider configuration:")
    print(f"configured={settings.qwen_ready}")
    print(f"model={settings.qwen_model}")

    require(
        settings.qwen_ready,
        "Qwen Provider 配置不完整",
    )

    response = await QwenProvider().invoke(
        role="announcement_verifier",
        system_prompt=(
            "你正在执行内部接口连通性测试。"
            "不要添加解释。"
        ),
        prompt="请只回复 QWEN_PROVIDER_OK。",
        temperature=0,
        max_tokens=32,
        enable_thinking=False,
    )

    print("\nQwen Provider response:")
    print(f"role={response.role}")
    print(f"provider={response.provider}")
    print(f"model={response.model}")
    print(f"latency_ms={response.latency_ms}")
    print(
        "finish_reason="
        f"{response.finish_reason}"
    )
    print(f"content={response.content}")
    print(f"usage={response.usage}")

    require(
        response.role == "announcement_verifier",
        "Provider 返回角色错误",
    )
    require(
        response.provider == "qwen",
        "Provider 名称错误",
    )
    require(
        bool(response.content),
        "Provider 返回内容为空",
    )
    require(
        "QWEN_PROVIDER_OK" in response.content,
        "Provider 未返回预期测试内容",
    )
    require(
        response.latency_ms > 0,
        "Provider 耗时记录异常",
    )
    require(
        response.attempts == 1,
        "Provider 调用次数异常",
    )
    require(
        response.validated is False,
        "尚未结构校验的响应不应标记为 validated",
    )

    print("\nQwen Provider checks passed")


def main() -> None:
    asyncio.run(check_provider())


if __name__ == "__main__":
    main()