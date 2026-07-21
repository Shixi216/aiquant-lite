from __future__ import annotations

import asyncio
from typing import Any

import httpx

from router.schemas import RouterInvokeResponse
from router.services.invocation import (
    RouterInvocationService,
)
from router.services.provider_retry import (
    ProviderRetryPolicy,
    is_transient_provider_error,
)


class FakeAuditStore:
    def __init__(self) -> None:
        self.records: list[
            dict[str, Any]
        ] = []

    def record_model_call(
        self,
        **values: Any,
    ) -> str:
        call_id = (
            f"call_{len(self.records) + 1}"
        )

        self.records.append(
            {
                "call_id": call_id,
                **values,
            }
        )

        return call_id


class FakeProvider:
    provider_name = "fake"

    def __init__(
        self,
        actions: list[
            RouterInvokeResponse | Exception
        ],
    ) -> None:
        self.actions = list(actions)
        self.call_count = 0

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> RouterInvokeResponse:
        del (
            role,
            prompt,
            system_prompt,
            temperature,
            max_tokens,
        )

        self.call_count += 1

        if not self.actions:
            raise RuntimeError(
                "FakeProvider 没有更多响应"
            )

        action = self.actions.pop(0)

        if isinstance(action, Exception):
            raise action

        return action


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def model_response() -> RouterInvokeResponse:
    return RouterInvokeResponse(
        role="news_processor",
        provider="fake",
        model="fake-model",
        content='{"items":[]}',
        latency_ms=100,
        finish_reason="stop",
        usage={
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
        validated=False,
        attempts=1,
    )


def wrapped_provider_error(
    cause: Exception,
) -> RuntimeError:
    error = RuntimeError(
        "Fake provider request failed: "
        f"{type(cause).__name__}"
    )

    error.__cause__ = cause

    return error


def create_service() -> tuple[
    RouterInvocationService,
    FakeAuditStore,
]:
    service = RouterInvocationService(
        retry_policy=ProviderRetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
        )
    )

    audit_store = FakeAuditStore()
    service.audit_store = audit_store

    return service, audit_store


async def check_timeout_then_success() -> None:
    request = httpx.Request(
        "POST",
        "https://example.invalid/v1/chat",
    )

    timeout = httpx.ReadTimeout(
        "read timed out",
        request=request,
    )

    provider = FakeProvider(
        [
            wrapped_provider_error(timeout),
            model_response(),
        ]
    )

    service, audit_store = (
        create_service()
    )

    response, call_ids = (
        await service
        ._invoke_provider_with_retry(
            task_id="task_retry",
            role="news_processor",
            model="fake-model",
            provider=provider,
            prompt="test",
            system_prompt="test",
            temperature=0,
            max_tokens=100,
        )
    )

    print("ReadTimeout retry:")
    print(
        f"provider_call_count="
        f"{provider.call_count}"
    )
    print(f"call_ids={call_ids}")
    audit_success = [
        record["success"]
        for record in audit_store.records
    ]

    print(
        "audit_success="
        f"{audit_success}"
    )
    print(
        "first_error_type="
        f"{audit_store.records[0]['error_type']}"
    )

    require(
        response.provider == "fake",
        "重试成功响应 Provider 错误",
    )

    require(
        provider.call_count == 2,
        "ReadTimeout 没有进行一次重试",
    )

    require(
        call_ids
        == [
            "call_1",
            "call_2",
        ],
        "物理调用 ID 记录错误",
    )

    require(
        [
            record["success"]
            for record in audit_store.records
        ]
        == [
            False,
            True,
        ],
        "失败与成功调用审计顺序错误",
    )

    require(
        audit_store.records[0][
            "error_type"
        ]
        == "ReadTimeout",
        "ReadTimeout 审计类型错误",
    )


async def check_unauthorized_no_retry() -> None:
    request = httpx.Request(
        "POST",
        "https://example.invalid/v1/chat",
    )

    response = httpx.Response(
        401,
        request=request,
    )

    unauthorized = httpx.HTTPStatusError(
        "unauthorized",
        request=request,
        response=response,
    )

    provider = FakeProvider(
        [
            wrapped_provider_error(
                unauthorized
            ),
            model_response(),
        ]
    )

    service, audit_store = (
        create_service()
    )

    rejected = False

    try:
        await service._invoke_provider_with_retry(
            task_id="task_unauthorized",
            role="news_processor",
            model="fake-model",
            provider=provider,
            prompt="test",
            system_prompt="test",
            temperature=0,
            max_tokens=100,
        )

    except RuntimeError:
        rejected = True

    print("\nHTTP 401 no retry:")
    print(f"rejected={rejected}")
    print(
        f"provider_call_count="
        f"{provider.call_count}"
    )
    print(
        f"audit_record_count="
        f"{len(audit_store.records)}"
    )
    print(
        "error_type="
        f"{audit_store.records[0]['error_type']}"
    )

    require(
        rejected,
        "HTTP 401 没有向上抛出",
    )

    require(
        provider.call_count == 1,
        "HTTP 401 被错误重试",
    )

    require(
        len(audit_store.records) == 1,
        "HTTP 401 产生了多余审计记录",
    )

    require(
        audit_store.records[0][
            "error_type"
        ]
        == "HTTPStatusError",
        "HTTP 401 审计类型错误",
    )


def check_status_classification() -> None:
    request = httpx.Request(
        "POST",
        "https://example.invalid/v1/chat",
    )

    response_503 = httpx.Response(
        503,
        request=request,
    )

    error_503 = httpx.HTTPStatusError(
        "service unavailable",
        request=request,
        response=response_503,
    )

    response_400 = httpx.Response(
        400,
        request=request,
    )

    error_400 = httpx.HTTPStatusError(
        "bad request",
        request=request,
        response=response_400,
    )

    transient_503 = (
        is_transient_provider_error(
            wrapped_provider_error(
                error_503
            )
        )
    )

    transient_400 = (
        is_transient_provider_error(
            wrapped_provider_error(
                error_400
            )
        )
    )

    print("\nHTTP classification:")
    print(
        f"status_503_transient="
        f"{transient_503}"
    )
    print(
        f"status_400_transient="
        f"{transient_400}"
    )

    require(
        transient_503,
        "HTTP 503 应被识别为瞬时错误",
    )

    require(
        not transient_400,
        "HTTP 400 不应被识别为瞬时错误",
    )


async def check_policy() -> None:
    await check_timeout_then_success()
    await check_unauthorized_no_retry()
    check_status_classification()

    print(
        "\nProvider retry policy checks passed"
    )


def main() -> None:
    asyncio.run(check_policy())


if __name__ == "__main__":
    main()