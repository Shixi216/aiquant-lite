from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4

from database.db import get_connection
from router.integration.sanitization import sanitize_error


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_text(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _token_value(value: Any) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return max(int(value), 0)

    return None


def normalize_token_usage(
    usage: Mapping[str, Any],
) -> tuple[int | None, int | None]:
    input_tokens = _token_value(
        usage.get("prompt_tokens")
    )

    if input_tokens is None:
        input_tokens = _token_value(
            usage.get("input_tokens")
        )

    output_tokens = _token_value(
        usage.get("completion_tokens")
    )

    if output_tokens is None:
        output_tokens = _token_value(
            usage.get("output_tokens")
        )

    return input_tokens, output_tokens


class AuditStore:
    """Short-lived DuckDB writes for Router audit records."""

    def create_task(
        self,
        *,
        task_type: str,
        request_payload: Mapping[str, Any],
        symbol: str | None = None,
        status: str = "running",
        task_id: str | None = None,
    ) -> str:
        resolved_task_id = (
            task_id or f"task_{uuid4().hex}"
        )
        now = utc_now()

        connection = get_connection()

        try:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id,
                    task_type,
                    symbol,
                    status,
                    request_json,
                    created_at,
                    updated_at
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    CAST(? AS JSON),
                    ?,
                    ?
                )
                """,
                [
                    resolved_task_id,
                    task_type,
                    symbol,
                    status,
                    json_text(request_payload),
                    now,
                    now,
                ],
            )
        finally:
            connection.close()

        return resolved_task_id

    def update_task_status(
        self,
        task_id: str,
        status: str,
    ) -> None:
        connection = get_connection()

        try:
            connection.execute(
                """
                UPDATE tasks
                SET
                    status = ?,
                    updated_at = ?
                WHERE task_id = ?
                """,
                [
                    status,
                    utc_now(),
                    task_id,
                ],
            )
        finally:
            connection.close()

    def record_model_call(
        self,
        *,
        task_id: str | None,
        agent_role: str,
        provider: str,
        model: str,
        usage: Mapping[str, Any],
        latency_ms: int | None,
        success: bool,
        estimated_cost: float | None = None,
        error_type: str | None = None,
        error_message: str | None = None,
        call_id: str | None = None,
        prompt_version: str | None = None,
        input_hash: str | None = None,
        retry_count: int = 0,
        schema_validation: str | None = None,
    ) -> str:
        resolved_call_id = (
            call_id or f"call_{uuid4().hex}"
        )
        input_tokens, output_tokens = (
            normalize_token_usage(usage)
        )

        safe_error = (
            sanitize_error(error_message)[:2000]
            if error_message
            else None
        )

        connection = get_connection()

        try:
            connection.execute(
                """
                INSERT INTO model_calls (
                    call_id,
                    task_id,
                    agent_role,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    estimated_cost,
                    success,
                    error_type,
                    error_message,
                    prompt_version,
                    input_hash,
                    retry_count,
                    schema_validation,
                    created_at
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?
                )
                """,
                [
                    resolved_call_id,
                    task_id,
                    agent_role,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    estimated_cost,
                    success,
                    error_type,
                    safe_error,
                    prompt_version,
                    input_hash,
                    retry_count,
                    schema_validation,
                    utc_now(),
                ],
            )
        finally:
            connection.close()

        return resolved_call_id

    def record_agent_result(
        self,
        *,
        task_id: str,
        agent_role: str,
        provider: str | None,
        model: str | None,
        result_payload: Mapping[str, Any] | None,
        success: bool,
        confidence: float | None = None,
        result_id: str | None = None,
    ) -> str:
        resolved_result_id = (
            result_id or f"result_{uuid4().hex}"
        )

        result_json = (
            json_text(result_payload)
            if result_payload is not None
            else None
        )

        connection = get_connection()

        try:
            connection.execute(
                """
                INSERT INTO agent_results (
                    result_id,
                    task_id,
                    agent_role,
                    provider,
                    model,
                    confidence,
                    success,
                    result_json,
                    created_at
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    CAST(? AS JSON),
                    ?
                )
                """,
                [
                    resolved_result_id,
                    task_id,
                    agent_role,
                    provider,
                    model,
                    confidence,
                    success,
                    result_json,
                    utc_now(),
                ],
            )
        finally:
            connection.close()

        return resolved_result_id
