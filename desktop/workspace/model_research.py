from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

import httpx

from router.integration.model_chain_logging import log_model_chain_stage
from router.schemas import ResearchSynthesisOutput


LOGGER = logging.getLogger("hermes_opc.model_chain")
ROLE = "research_synthesizer"
PROVIDER = "longcat"


class ResearchModelClient(Protocol):
    def synthesize(
        self,
        *,
        request_id: str,
        symbol: str,
        data_cutoff: datetime,
        local_research: dict[str, Any],
    ) -> "ResearchModelResult": ...


class ResearchModelError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchModelResult:
    task_id: str
    provider: str
    model: str
    call_ids: tuple[str, ...]
    output: ResearchSynthesisOutput

    @property
    def model_call_count(self) -> int:
        return len(self.call_ids)


class RouterResearchClient:
    def __init__(
        self,
        *,
        router_url: str = "http://127.0.0.1:8765",
        timeout: float = 120,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.router_url = router_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.timeout,
            trust_env=False,
            transport=self.transport,
        )

    def synthesize(
        self,
        *,
        request_id: str,
        symbol: str,
        data_cutoff: datetime,
        local_research: dict[str, Any],
    ) -> ResearchModelResult:
        registered = False
        credential_loaded = False
        response_status: int | None = None
        try:
            with self._client() as client:
                role_response = client.get(
                    f"{self.router_url}/v1/roles/{ROLE}"
                )
                response_status = role_response.status_code
                registered = role_response.status_code == 200
                role_payload = (
                    role_response.json()
                    if registered
                    else {}
                )
                credential_loaded = bool(
                    isinstance(role_payload, dict)
                    and role_payload.get("enabled")
                )
                if not registered or not credential_loaded:
                    raise ResearchModelError("NOT_CONFIGURED")

                prompt = json.dumps(
                    {
                        "request_id": request_id,
                        "analysis_mode": "RESEARCH",
                        "symbol": symbol,
                        "data_cutoff": data_cutoff.isoformat(),
                        "local_research": local_research,
                        "formal_decision_requested": False,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                response = client.post(
                    f"{self.router_url}/v1/invoke",
                    json={
                        "request_id": request_id,
                        "role": ROLE,
                        "symbol": symbol,
                        "prompt": prompt,
                        "temperature": 0.1,
                        "max_tokens": 1800,
                        "risk_level": "medium",
                        "budget_tier": "standard",
                    },
                )
                response_status = response.status_code
                if response.status_code != 200:
                    raise ResearchModelError(
                        "NOT_CONFIGURED"
                        if response.status_code == 503
                        and not credential_loaded
                        else "PROVIDER_ERROR"
                    )
                payload = response.json()
        except ResearchModelError as exc:
            log_model_chain_stage(
                logger=LOGGER,
                request_id=request_id,
                skill_id="stock_research",
                provider_id=PROVIDER,
                provider_registered=registered,
                credential_loaded=credential_loaded,
                request_started=True,
                response_received=response_status is not None,
                response_status=response_status,
                audit_task_created=(
                    response_status is not None
                    and response_status not in {404, 422}
                ),
                error_code=str(exc),
                sanitized_error=str(exc),
            )
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            log_model_chain_stage(
                logger=LOGGER,
                request_id=request_id,
                skill_id="stock_research",
                provider_id=PROVIDER,
                provider_registered=registered,
                credential_loaded=credential_loaded,
                request_started=True,
                response_received=response_status is not None,
                response_status=response_status,
                audit_task_created=False,
                error_code="NETWORK_ERROR",
                sanitized_error=exc,
            )
            raise ResearchModelError("NETWORK_ERROR") from exc

        try:
            call_ids = tuple(
                value
                for value in payload.get("call_ids", [])
                if isinstance(value, str) and value
            )
            task_id = str(payload.get("task_id") or "")
            content = payload.get("content")
            if (
                not task_id
                or not call_ids
                or payload.get("validated") is not True
                or not isinstance(content, str)
            ):
                raise ValueError("invalid Router research response")
            output = ResearchSynthesisOutput.model_validate_json(content)
            if output.symbol != symbol:
                raise ValueError("research symbol mismatch")
        except (ValueError, TypeError) as exc:
            log_model_chain_stage(
                logger=LOGGER,
                request_id=request_id,
                skill_id="stock_research",
                provider_id=PROVIDER,
                provider_registered=registered,
                credential_loaded=credential_loaded,
                request_started=True,
                response_received=True,
                response_status=200,
                audit_task_created=bool(payload.get("task_id")),
                error_code="INVALID_MODEL_RESPONSE",
                sanitized_error=exc,
            )
            raise ResearchModelError("INVALID_MODEL_RESPONSE") from exc

        log_model_chain_stage(
            logger=LOGGER,
            request_id=request_id,
            skill_id="stock_research",
            provider_id=PROVIDER,
            provider_registered=registered,
            credential_loaded=credential_loaded,
            request_started=True,
            response_received=True,
            response_status=200,
            audit_task_created=True,
        )
        return ResearchModelResult(
            task_id=task_id,
            provider=str(payload.get("provider") or PROVIDER),
            model=str(payload.get("model") or ""),
            call_ids=call_ids,
            output=output,
        )


__all__ = [
    "ResearchModelClient",
    "ResearchModelError",
    "ResearchModelResult",
    "RouterResearchClient",
]
