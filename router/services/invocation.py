from __future__ import annotations

import asyncio
from time import perf_counter

from router.registry import get_role
from router.schemas import (
    RouterInvokeRequest,
    RouterInvokeResponse,
)
from router.services.audit import AuditStore
from router.services.model_provider import (
    RouterModelProvider,
)
from router.services.provider_registry import (
    get_model_provider,
)
from router.services.provider_retry import (
    DEFAULT_PROVIDER_RETRY_POLICY,
    AuditedProviderCallError,
    ProviderCallSequenceError,
    ProviderRetryPolicy,
    is_transient_provider_error,
    provider_error_type,
)
from router.services.role_handler_registry import (
    get_role_handler,
)


class RouterInvocationService:
    """Dispatch and audit specialist model requests."""

    def __init__(
        self,
        *,
        retry_policy: (
            ProviderRetryPolicy | None
        ) = None,
    ) -> None:
        self.audit_store = AuditStore()

        self.retry_policy = (
            retry_policy
            or DEFAULT_PROVIDER_RETRY_POLICY
        )

    async def _invoke_provider(
        self,
        *,
        task_id: str,
        role: str,
        model: str,
        provider: RouterModelProvider,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[RouterInvokeResponse, str]:
        started = perf_counter()

        try:
            response = await provider.invoke(
                role=role,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        except Exception as exc:
            latency_ms = round(
                (perf_counter() - started) * 1000
            )

            call_id = (
                self.audit_store.record_model_call(
                    task_id=task_id,
                    agent_role=role,
                    provider=provider.provider_name,
                    model=model,
                    usage={},
                    latency_ms=latency_ms,
                    success=False,
                    error_type=provider_error_type(
                        exc
                    ),
                    error_message=str(exc),
                )
            )

            raise AuditedProviderCallError(
                call_id=call_id,
                original_exception=exc,
            ) from exc

        call_id = self.audit_store.record_model_call(
            task_id=task_id,
            agent_role=role,
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
            success=True,
        )

        return response, call_id

    async def _invoke_provider_with_retry(
        self,
        *,
        task_id: str,
        role: str,
        model: str,
        provider: RouterModelProvider,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[
        RouterInvokeResponse,
        list[str],
    ]:
        physical_call_ids: list[str] = []

        for attempt in range(
            1,
            self.retry_policy.max_attempts + 1,
        ):
            try:
                response, call_id = (
                    await self._invoke_provider(
                        task_id=task_id,
                        role=role,
                        model=model,
                        provider=provider,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                )

            except AuditedProviderCallError as exc:
                physical_call_ids.append(
                    exc.call_id
                )

                original = (
                    exc.original_exception
                )

                retryable = (
                    is_transient_provider_error(
                        original
                    )
                )

                exhausted = (
                    attempt
                    >= self.retry_policy.max_attempts
                )

                if not retryable or exhausted:
                    raise ProviderCallSequenceError(
                        call_ids=list(
                            physical_call_ids
                        ),
                        original_exception=original,
                    ) from exc

                delay = (
                    self.retry_policy
                    .delay_after_failure(attempt)
                )

                if delay > 0:
                    await asyncio.sleep(delay)

                continue

            physical_call_ids.append(call_id)

            return (
                response,
                physical_call_ids,
            )

        raise RuntimeError(
            "Provider 重试循环意外结束"
        )

    async def invoke(
        self,
        request: RouterInvokeRequest,
    ) -> RouterInvokeResponse:
        role = get_role(request.role)

        if role is None:
            raise ValueError(
                f"未知 Router 角色：{request.role}"
            )

        if not role.enabled:
            raise RuntimeError(
                f"角色尚未启用：{request.role}"
            )

        handler = get_role_handler(
            role.role
        )

        if handler is None:
            raise RuntimeError(
                f"角色 Handler 尚未接入：{request.role}"
            )

        provider = get_model_provider(
            role.provider
        )

        if provider is None:
            raise RuntimeError(
                "通用调用尚未接入该 Provider："
                f"{role.provider}"
            )

        task_id = self.audit_store.create_task(
            task_type="router_invoke",
            symbol=request.symbol,
            request_payload=request.model_dump(
                mode="json",
                exclude_none=True,
            ),
        )

        call_ids: list[str] = []

        async def invoke_model(
            *,
            prompt: str,
            system_prompt: str,
            temperature: float,
            max_tokens: int,
        ) -> tuple[RouterInvokeResponse, str]:
            try:
                response, physical_call_ids = (
                    await self
                    ._invoke_provider_with_retry(
                        task_id=task_id,
                        role=role.role,
                        model=role.preferred_model,
                        provider=provider,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                )

            except ProviderCallSequenceError as exc:
                call_ids.extend(
                    exc.call_ids
                )

                raise

            call_ids.extend(
                physical_call_ids
            )

            successful_call_id = (
                physical_call_ids[-1]
            )

            return (
                response,
                successful_call_id,
            )

        try:
            result = await handler.run(
                request=request,
                task_id=task_id,
                invoke_model=invoke_model,
            )

            final_response = (
                result.response.model_copy(
                    update={
                        "call_ids": list(call_ids),
                    }
                )
            )

            self.audit_store.record_agent_result(
                task_id=task_id,
                agent_role=role.role,
                provider=final_response.provider,
                model=final_response.model,
                confidence=result.confidence,
                success=True,
                result_payload={
                    "validated": (
                        final_response.validated
                    ),
                    "attempts": (
                        final_response.attempts
                    ),
                    "call_ids": (
                        final_response.call_ids
                    ),
                    "output": result.result_payload,
                },
            )

            self.audit_store.update_task_status(
                task_id,
                "completed",
            )

            return final_response

        except Exception as exc:
            self.audit_store.record_agent_result(
                task_id=task_id,
                agent_role=role.role,
                provider=role.provider,
                model=role.preferred_model,
                confidence=None,
                success=False,
                result_payload={
                    "error_type": (
                        provider_error_type(exc)
                    ),
                    "error_message": (
                        str(exc)[:2000]
                    ),
                    "call_ids": call_ids,
                },
            )

            self.audit_store.update_task_status(
                task_id,
                "failed",
            )

            raise