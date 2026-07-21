from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import httpx

import router.services.invocation as invocation_module
from router.schemas import RouterInvokeRequest
from router.services.invocation import (
    RouterInvocationService,
)
from router.services.provider_retry import (
    ProviderRetryPolicy,
)


class FakeAuditStore:
    def __init__(self) -> None:
        self.model_calls: list[
            dict[str, Any]
        ] = []

        self.agent_results: list[
            dict[str, Any]
        ] = []

        self.statuses: dict[str, str] = {}

    def create_task(
        self,
        **values: Any,
    ) -> str:
        del values
        return "task_failure"

    def record_model_call(
        self,
        **values: Any,
    ) -> str:
        call_id = (
            f"call_{len(self.model_calls) + 1}"
        )

        self.model_calls.append(
            {
                "call_id": call_id,
                **values,
            }
        )

        return call_id

    def record_agent_result(
        self,
        **values: Any,
    ) -> str:
        result_id = (
            f"result_{len(self.agent_results) + 1}"
        )

        self.agent_results.append(
            {
                "result_id": result_id,
                **values,
            }
        )

        return result_id

    def update_task_status(
        self,
        task_id: str,
        status: str,
    ) -> None:
        self.statuses[task_id] = status


class AlwaysTimeoutProvider:
    provider_name = "fake"

    def __init__(self) -> None:
        self.call_count = 0

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> Any:
        del (
            role,
            prompt,
            system_prompt,
            temperature,
            max_tokens,
        )

        self.call_count += 1

        request = httpx.Request(
            "POST",
            "https://example.invalid/v1/chat",
        )

        timeout = httpx.ReadTimeout(
            "read timed out",
            request=request,
        )

        error = RuntimeError(
            "Fake provider network error"
        )

        error.__cause__ = timeout

        raise error


class InvokeOnceHandler:
    async def run(
        self,
        *,
        request: RouterInvokeRequest,
        task_id: str,
        invoke_model: Any,
    ) -> Any:
        del request, task_id

        await invoke_model(
            prompt="test",
            system_prompt="test",
            temperature=0,
            max_tokens=100,
        )

        raise RuntimeError(
            "Handler should not reach this line"
        )


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


async def check_failure_audit() -> None:
    provider = AlwaysTimeoutProvider()
    handler = InvokeOnceHandler()
    audit_store = FakeAuditStore()

    role = SimpleNamespace(
        role="news_processor",
        enabled=True,
        provider="fake",
        preferred_model="fake-model",
    )

    original_get_role = (
        invocation_module.get_role
    )

    original_get_role_handler = (
        invocation_module.get_role_handler
    )

    original_get_model_provider = (
        invocation_module.get_model_provider
    )

    def fake_get_role(
        role_name: str,
    ) -> Any:
        del role_name
        return role

    def fake_get_role_handler(
        role_name: str,
    ) -> Any:
        del role_name
        return handler

    def fake_get_model_provider(
        provider_name: str,
    ) -> Any:
        del provider_name
        return provider

    invocation_module.get_role = (
        fake_get_role
    )

    invocation_module.get_role_handler = (
        fake_get_role_handler
    )

    invocation_module.get_model_provider = (
        fake_get_model_provider
    )

    service = RouterInvocationService(
        retry_policy=ProviderRetryPolicy(
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
        )
    )

    service.audit_store = audit_store

    rejected = False

    try:
        await service.invoke(
            RouterInvokeRequest(
                role="news_processor",
                prompt="test",
                temperature=0,
                max_tokens=100,
            )
        )

    except RuntimeError:
        rejected = True

    finally:
        invocation_module.get_role = (
            original_get_role
        )

        invocation_module.get_role_handler = (
            original_get_role_handler
        )

        invocation_module.get_model_provider = (
            original_get_model_provider
        )

    model_call_ids = [
        record["call_id"]
        for record in audit_store.model_calls
    ]

    agent_result = (
        audit_store.agent_results[0]
    )

    result_payload = agent_result[
        "result_payload"
    ]

    agent_call_ids = result_payload[
        "call_ids"
    ]

    model_success = [
        record["success"]
        for record in audit_store.model_calls
    ]

    print("Terminal retry failure audit:")
    print(f"rejected={rejected}")
    print(
        "provider_call_count="
        f"{provider.call_count}"
    )
    print(
        "model_call_count="
        f"{len(audit_store.model_calls)}"
    )
    print(
        "agent_result_count="
        f"{len(audit_store.agent_results)}"
    )
    print(
        f"model_call_ids={model_call_ids}"
    )
    print(
        f"agent_call_ids={agent_call_ids}"
    )
    print(
        f"model_success={model_success}"
    )
    print(
        "agent_success="
        f"{agent_result['success']}"
    )
    print(
        "agent_error_type="
        f"{result_payload['error_type']}"
    )
    print(
        "task_status="
        f"{audit_store.statuses['task_failure']}"
    )

    require(
        rejected,
        "最终超时没有向上抛出",
    )

    require(
        provider.call_count == 2,
        "最终超时物理调用次数错误",
    )

    require(
        model_call_ids
        == [
            "call_1",
            "call_2",
        ],
        "失败模型调用 ID 错误",
    )

    require(
        agent_call_ids == model_call_ids,
        (
            "Agent 失败结果没有关联"
            "全部物理模型调用"
        ),
    )

    require(
        model_success
        == [
            False,
            False,
        ],
        "最终失败的模型审计状态错误",
    )

    require(
        len(audit_store.agent_results) == 1,
        "最终失败 Agent 结果数量错误",
    )

    require(
        agent_result["success"] is False,
        "最终失败 Agent 结果状态错误",
    )

    require(
        result_payload["error_type"]
        == "ReadTimeout",
        "最终失败错误类型错误",
    )

    require(
        audit_store.statuses[
            "task_failure"
        ]
        == "failed",
        "最终失败任务状态错误",
    )

    print(
        "\nProvider retry failure "
        "audit checks passed"
    )


def main() -> None:
    asyncio.run(
        check_failure_audit()
    )


if __name__ == "__main__":
    main()