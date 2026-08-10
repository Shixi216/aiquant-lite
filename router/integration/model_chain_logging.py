from __future__ import annotations

import json
import logging
from typing import Any

from router.integration.sanitization import sanitize_error


MODEL_CHAIN_FIELDS = (
    "request_id",
    "skill_id",
    "provider_id",
    "provider_registered",
    "credential_loaded",
    "request_started",
    "response_received",
    "response_status",
    "audit_task_created",
    "error_code",
    "sanitized_error",
)


def model_chain_stage_payload(
    *,
    request_id: str,
    skill_id: str,
    provider_id: str | None,
    provider_registered: bool | None,
    credential_loaded: bool | None,
    request_started: bool,
    response_received: bool,
    response_status: int | None,
    audit_task_created: bool | None,
    error_code: str | None = None,
    sanitized_error: Exception | str | None = None,
) -> dict[str, Any]:
    """Build the fixed, credential-free model-chain stage payload."""

    return {
        "request_id": request_id,
        "skill_id": skill_id,
        "provider_id": provider_id,
        "provider_registered": provider_registered,
        "credential_loaded": credential_loaded,
        "request_started": request_started,
        "response_received": response_received,
        "response_status": response_status,
        "audit_task_created": audit_task_created,
        "error_code": error_code,
        "sanitized_error": (
            sanitize_error(sanitized_error)
            if sanitized_error is not None
            else None
        ),
    }


def log_model_chain_stage(
    *,
    logger: logging.Logger,
    **values: Any,
) -> dict[str, Any]:
    payload = model_chain_stage_payload(**values)
    logger.info(
        "model_chain_stage %s",
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    return payload


__all__ = [
    "MODEL_CHAIN_FIELDS",
    "log_model_chain_stage",
    "model_chain_stage_payload",
]
