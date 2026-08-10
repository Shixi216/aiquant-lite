from __future__ import annotations

import logging
from time import perf_counter

from router.config import router_settings
from router.integration.model_chain_logging import log_model_chain_stage
from router.integration.identifiers import new_request_id
from router.integration.sanitization import sanitize_error
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
from router.services.provider_registry import list_provider_names
from router.services.provider_retry import provider_error_type


ROLE_NAME = "announcement_verifier"
LOGGER = logging.getLogger("uvicorn.error")


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
        request_id: str,
        audit_store: AuditStore,
    ) -> None:
        self.task_id = task_id
        self.request_id = request_id
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
            log_model_chain_stage(
                logger=LOGGER,
                request_id=self.request_id,
                skill_id="announcement_verification",
                provider_id="qwen",
                provider_registered=True,
                credential_loaded=router_settings.qwen_ready,
                request_started=True,
                response_received=False,
                response_status=None,
                audit_task_created=True,
                error_code=provider_error_type(exc),
                sanitized_error=exc,
            )
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
        log_model_chain_stage(
            logger=LOGGER,
            request_id=self.request_id,
            skill_id="announcement_verification",
            provider_id=response.provider,
            provider_registered=True,
            credential_loaded=router_settings.qwen_ready,
            request_started=True,
            response_received=True,
            response_status=200,
            audit_task_created=True,
        )

        return response


class AnnouncementVerificationPipelineService:
    """Run exact official announcement verification."""

    def __init__(self) -> None:
        self.audit_store = AuditStore()

    async def run(
        self,
        request: AnnouncementVerificationPipelineRequest,
    ) -> AnnouncementVerificationPipelineResponse:
        request_id = new_request_id()
        provider_registered = "qwen" in list_provider_names()
        try:
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
        except Exception as exc:
            log_model_chain_stage(
                logger=LOGGER,
                request_id=request_id,
                skill_id="announcement_verification",
                provider_id="qwen",
                provider_registered=provider_registered,
                credential_loaded=router_settings.qwen_ready,
                request_started=False,
                response_received=False,
                response_status=None,
                audit_task_created=False,
                error_code="AUDIT_TASK_CREATE_FAILED",
                sanitized_error=exc,
            )
            raise RuntimeError(
                "AUDIT_TASK_CREATE_FAILED"
            ) from exc

        log_model_chain_stage(
            logger=LOGGER,
            request_id=request_id,
            skill_id="announcement_verification",
            provider_id="qwen",
            provider_registered=provider_registered,
            credential_loaded=router_settings.qwen_ready,
            request_started=False,
            response_received=False,
            response_status=None,
            audit_task_created=True,
        )

        provider = AuditedQwenProvider(
            task_id=task_id,
            request_id=request_id,
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
                    "error_type": provider_error_type(exc),
                    "error_message": sanitize_error(exc),
                    "call_ids": provider.call_ids,
                },
            )

            self.audit_store.update_task_status(
                task_id,
                "failed",
            )
            if not provider.call_ids:
                log_model_chain_stage(
                    logger=LOGGER,
                    request_id=request_id,
                    skill_id="announcement_verification",
                    provider_id="qwen",
                    provider_registered=provider_registered,
                    credential_loaded=router_settings.qwen_ready,
                    request_started=False,
                    response_received=False,
                    response_status=None,
                    audit_task_created=True,
                    error_code=provider_error_type(exc),
                    sanitized_error=exc,
                )

            raise
