from __future__ import annotations

import asyncio
from time import perf_counter

from router.registry import get_role
from router.schemas import (
    RiskReviewOutput,
    RiskReviewResult,
    RiskReviewStatus,
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
from router.services.risk_controller_handler import build_risk_review_prompt
from router.services.routing_policy import (
    ModelCallBudgetExceeded,
    RoutingPolicyService,
)


class RouterInvocationService:
    """Dispatch and audit specialist model requests."""

    def __init__(
        self,
        *,
        retry_policy: (
            ProviderRetryPolicy | None
        ) = None,
        routing_policy: RoutingPolicyService | None = None,
    ) -> None:
        self.audit_store = AuditStore()

        self.retry_policy = (
            retry_policy
            or DEFAULT_PROVIDER_RETRY_POLICY
        )
        self.routing_policy = routing_policy or RoutingPolicyService()

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
        max_attempts: int,
    ) -> tuple[
        RouterInvokeResponse,
        list[str],
    ]:
        physical_call_ids: list[str] = []

        for attempt in range(
            1,
            max_attempts + 1,
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
                    >= max_attempts
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

    async def _run_risk_review(
        self,
        *,
        request: RouterInvokeRequest,
        task_id: str,
        primary_role: str,
        primary_response: RouterInvokeResponse,
        max_physical_calls: int,
        call_ids: list[str],
    ) -> RiskReviewResult | None:
        if request.risk_level.value not in {"high", "critical"}:
            return None
        if primary_role == "risk_controller":
            return None

        risk_role = get_role("risk_controller")
        if risk_role is None or not risk_role.enabled:
            return RiskReviewResult(
                status=RiskReviewStatus.UNAVAILABLE,
                reason="risk_controller is not configured; human review remains required",
            )

        handler = get_role_handler(risk_role.role)
        provider = get_model_provider(risk_role.provider)
        if handler is None or provider is None:
            return RiskReviewResult(
                status=RiskReviewStatus.UNAVAILABLE,
                reason="risk_controller implementation is unavailable; human review remains required",
            )

        remaining_calls = max_physical_calls - len(call_ids)
        if remaining_calls <= 0:
            return RiskReviewResult(
                status=RiskReviewStatus.BUDGET_EXHAUSTED,
                reason="model call budget exhausted; human review remains required",
            )

        review_request = RouterInvokeRequest(
            role="risk_controller",
            symbol=request.symbol,
            prompt=build_risk_review_prompt(
                original_role=primary_role,
                symbol=request.symbol,
                risk_level=request.risk_level.value,
                original_prompt=request.prompt,
                original_output=primary_response.content,
            ),
            temperature=0,
            max_tokens=min(1024, remaining_calls * 512),
            risk_level=request.risk_level,
            budget_tier=request.budget_tier,
        )
        review_call_ids: list[str] = []

        async def invoke_review_model(
            *,
            prompt: str,
            system_prompt: str,
            temperature: float,
            max_tokens: int,
        ) -> tuple[RouterInvokeResponse, str]:
            available = max_physical_calls - len(call_ids)
            if available <= 0:
                raise ModelCallBudgetExceeded("risk review model call budget exhausted")
            try:
                response, physical_call_ids = await self._invoke_provider_with_retry(
                    task_id=task_id,
                    role=risk_role.role,
                    model=risk_role.preferred_model,
                    provider=provider,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_attempts=min(self.retry_policy.max_attempts, available),
                )
            except ProviderCallSequenceError as exc:
                ids = list(exc.call_ids)
                call_ids.extend(ids)
                review_call_ids.extend(ids)
                raise
            call_ids.extend(physical_call_ids)
            review_call_ids.extend(physical_call_ids)
            return response, physical_call_ids[-1]

        try:
            result = await handler.run(
                request=review_request,
                task_id=task_id,
                invoke_model=invoke_review_model,
            )
            output = RiskReviewOutput.model_validate(result.result_payload)
        except ModelCallBudgetExceeded:
            return RiskReviewResult(
                status=RiskReviewStatus.BUDGET_EXHAUSTED,
                call_ids=review_call_ids,
                reason="model call budget exhausted; human review remains required",
            )
        except ProviderCallSequenceError as exc:
            review = RiskReviewResult(
                status=RiskReviewStatus.FAILED,
                provider=risk_role.provider,
                model=risk_role.preferred_model,
                call_ids=review_call_ids,
                reason=(
                    "risk_controller provider failed: "
                    f"{provider_error_type(exc.original_exception)}; human review remains required"
                ),
            )
            self.audit_store.record_agent_result(
                task_id=task_id,
                agent_role=risk_role.role,
                provider=risk_role.provider,
                model=risk_role.preferred_model,
                confidence=None,
                success=False,
                result_payload=review.model_dump(mode="json"),
            )
            return review
        except Exception as exc:
            review = RiskReviewResult(
                status=RiskReviewStatus.FAILED,
                provider=risk_role.provider,
                model=risk_role.preferred_model,
                call_ids=review_call_ids,
                reason=(
                    "risk_controller validation failed: "
                    f"{provider_error_type(exc)}; human review remains required"
                ),
            )
            self.audit_store.record_agent_result(
                task_id=task_id,
                agent_role=risk_role.role,
                provider=risk_role.provider,
                model=risk_role.preferred_model,
                confidence=None,
                success=False,
                result_payload=review.model_dump(mode="json"),
            )
            return review

        review = RiskReviewResult(
            status=RiskReviewStatus.COMPLETED,
            provider=result.response.provider,
            model=result.response.model,
            call_ids=review_call_ids,
            output=output,
        )
        self.audit_store.record_agent_result(
            task_id=task_id,
            agent_role=risk_role.role,
            provider=result.response.provider,
            model=result.response.model,
            confidence=output.confidence,
            success=True,
            result_payload=review.model_dump(mode="json"),
        )
        return review

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

        routing = self.routing_policy.decide(
            role=role,
            risk_level=request.risk_level,
            budget_tier=request.budget_tier,
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
            remaining_calls = (
                routing.max_physical_calls - len(call_ids)
            )
            if remaining_calls <= 0:
                raise ModelCallBudgetExceeded(
                    "model call budget exhausted before provider invocation"
                )

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
                        temperature=min(
                            temperature,
                            routing.temperature_cap,
                        ),
                        max_tokens=min(
                            max_tokens,
                            routing.max_output_tokens_per_call,
                        ),
                        max_attempts=min(
                            self.retry_policy.max_attempts,
                            remaining_calls,
                        ),
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

            risk_review = await self._run_risk_review(
                request=request,
                task_id=task_id,
                primary_role=role.role,
                primary_response=result.response,
                max_physical_calls=routing.max_physical_calls,
                call_ids=call_ids,
            )

            final_response = (
                result.response.model_copy(
                    update={
                        "call_ids": list(call_ids),
                        "routing": routing,
                        "risk_review": risk_review,
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
                    "routing": routing.model_dump(
                        mode="json"
                    ),
                    "risk_review": (
                        risk_review.model_dump(mode="json")
                        if risk_review is not None
                        else None
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
                    "routing": routing.model_dump(
                        mode="json"
                    ),
                },
            )

            self.audit_store.update_task_status(
                task_id,
                "failed",
            )

            raise
