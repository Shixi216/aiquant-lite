from __future__ import annotations

from time import perf_counter

from router.config import router_settings
from router.providers import QwenProvider
from router.schemas import (
    AnnouncementVerificationPipelineRequest,
    AnnouncementVerificationPipelineResponse,
    RouterInvokeResponse,
)
from router.services.announcement_evidence import (
    AnnouncementEvidenceService,
)
from router.services.announcement_verifier import (
    AnnouncementVerifierService,
)
from router.services.audit import AuditStore


ROLE_NAME = "announcement_verifier"


def merge_response_usage(
    responses: tuple[RouterInvokeResponse, ...],
) -> dict[str, int]:
    merged: dict[str, int] = {}

    for response in responses:
        for key, value in response.usage.items():
            merged[key] = merged.get(key, 0) + value

    return merged


def average_confidence(
    values: list[float],
) -> float | None:
    if not values:
        return None

    return sum(values) / len(values)


class AuditedQwenProvider(QwenProvider):
    """Qwen provider that records every physical call."""

    def __init__(
        self,
        *,
        task_id: str,
        audit_store: AuditStore,
    ) -> None:
        self.task_id = task_id
        self.audit_store = audit_store
        self.call_ids: list[str] = []

    async def invoke(
        self,
        *,
        role: str,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        enable_thinking: bool = False,
    ) -> RouterInvokeResponse:
        started = perf_counter()

        try:
            response = await super().invoke(
                role=role,
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            )

        except Exception as exc:
            latency_ms = round(
                (perf_counter() - started) * 1000
            )

            call_id = (
                self.audit_store.record_model_call(
                    task_id=self.task_id,
                    agent_role=role,
                    provider=self.provider_name,
                    model=router_settings.qwen_model,
                    usage={},
                    latency_ms=latency_ms,
                    success=False,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
            )

            self.call_ids.append(call_id)
            raise

        call_id = self.audit_store.record_model_call(
            task_id=self.task_id,
            agent_role=role,
            provider=response.provider,
            model=response.model,
            usage=response.usage,
            latency_ms=response.latency_ms,
            success=True,
        )

        self.call_ids.append(call_id)

        return response


class AnnouncementVerificationPipelineService:
    """Run exact official announcement verification."""

    def __init__(self) -> None:
        self.audit_store = AuditStore()

    async def run(
        self,
        request: AnnouncementVerificationPipelineRequest,
    ) -> AnnouncementVerificationPipelineResponse:
        task_id = self.audit_store.create_task(
            task_type=(
                "announcement_verification_pipeline"
            ),
            symbol=request.symbol,
            request_payload=request.model_dump(
                mode="json",
                exclude_none=True,
            ),
        )

        provider = AuditedQwenProvider(
            task_id=task_id,
            audit_store=self.audit_store,
        )

        try:
            bundle = (
                await AnnouncementEvidenceService()
                .build_selected(
                    symbol=request.symbol,
                    record_id=request.record_id,
                    announcement_id=(
                        request.announcement_id
                    ),
                )
            )

            run = (
                await AnnouncementVerifierService(
                    provider=provider
                ).verify(
                    bundle=bundle,
                    claims=request.claims,
                    max_tokens=request.max_tokens,
                )
            )

            final_response = run.responses[-1]
            output = run.output
            usage = merge_response_usage(
                run.responses
            )

            confidence = average_confidence(
                [
                    claim.confidence
                    for claim in output.claims
                ]
            )

            result_payload = {
                "validated": True,
                "attempts": run.attempts,
                "call_ids": provider.call_ids,
                "evidence": {
                    "record_id": bundle.record_id,
                    "announcement_id": (
                        bundle.announcement_id
                    ),
                    "source_url": bundle.source_url,
                    "pdf_url": bundle.pdf_url,
                    "pdf_sha256": bundle.pdf_sha256,
                    "text_sha256": (
                        bundle.text_sha256
                    ),
                    "page_count": bundle.page_count,
                },
                "output": output.model_dump(
                    mode="json"
                ),
            }

            self.audit_store.record_agent_result(
                task_id=task_id,
                agent_role=ROLE_NAME,
                provider=final_response.provider,
                model=final_response.model,
                confidence=confidence,
                success=True,
                result_payload=result_payload,
            )

            self.audit_store.update_task_status(
                task_id,
                "completed",
            )

            return (
                AnnouncementVerificationPipelineResponse(
                    symbol=request.symbol,
                    start_date=request.start_date,
                    end_date=request.end_date,
                    record_id=bundle.record_id,
                    announcement_id=(
                        bundle.announcement_id
                    ),
                    announcement_date=(
                        bundle.announcement_date
                    ),
                    title=bundle.title,
                    source_url=bundle.source_url,
                    pdf_url=bundle.pdf_url,
                    pdf_sha256=bundle.pdf_sha256,
                    text_sha256=bundle.text_sha256,
                    task_id=task_id,
                    call_ids=list(provider.call_ids),
                    attempts=run.attempts,
                    validated=True,
                    provider=final_response.provider,
                    model=final_response.model,
                    latency_ms=run.total_latency_ms,
                    usage=usage,
                    output=output,
                )
            )

        except Exception as exc:
            self.audit_store.record_agent_result(
                task_id=task_id,
                agent_role=ROLE_NAME,
                provider="qwen",
                model=router_settings.qwen_model,
                confidence=None,
                success=False,
                result_payload={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:2000],
                    "call_ids": provider.call_ids,
                },
            )

            self.audit_store.update_task_status(
                task_id,
                "failed",
            )

            raise