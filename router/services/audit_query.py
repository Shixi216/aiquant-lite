from __future__ import annotations

import json
import time
from typing import Any

from pydantic import ValidationError

from database.db import get_connection
from router.schemas.audit import (
    AgentResultAudit,
    ModelCallAudit,
    TaskAuditDetail,
    TaskAuditListResponse,
    TaskAuditSummary,
)


def decode_json_object(
    value: Any,
) -> dict[str, Any] | None:
    if value is None:
        return None

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        parsed = json.loads(value)

        if isinstance(parsed, dict):
            return parsed

    raise RuntimeError(
        "DuckDB JSON value is not an object"
    )


class AuditQueryStore:
    """Read-only access to Router audit records."""

    @staticmethod
    def _connect():
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                return get_connection(read_only=True)
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    time.sleep(0.05 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def get_task(
        self,
        task_id: str,
    ) -> TaskAuditDetail | None:
        connection = self._connect()

        try:
            task_row = connection.execute(
                """
                SELECT
                    task_id,
                    task_type,
                    symbol,
                    status,
                    request_json,
                    created_at,
                    updated_at
                FROM tasks
                WHERE task_id = ?
                """,
                [task_id],
            ).fetchone()

            if task_row is None:
                return None

            call_rows = connection.execute(
                """
                SELECT
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
                FROM model_calls
                WHERE task_id = ?
                ORDER BY created_at, call_id
                """,
                [task_id],
            ).fetchall()

            result_rows = connection.execute(
                """
                SELECT
                    result_id,
                    task_id,
                    agent_role,
                    provider,
                    model,
                    confidence,
                    success,
                    result_json,
                    created_at
                FROM agent_results
                WHERE task_id = ?
                ORDER BY created_at, result_id
                """,
                [task_id],
            ).fetchall()

        finally:
            connection.close()

        model_calls = [
            ModelCallAudit(
                call_id=row[0],
                task_id=row[1],
                agent_role=row[2],
                provider=row[3],
                model=row[4],
                input_tokens=row[5],
                output_tokens=row[6],
                latency_ms=row[7],
                estimated_cost=row[8],
                success=row[9],
                error_type=row[10],
                error_message=row[11],
                prompt_version=row[12],
                input_hash=row[13],
                retry_count=row[14],
                schema_validation=row[15],
                created_at=row[16],
            )
            for row in call_rows
        ]

        agent_results = [
            AgentResultAudit(
                result_id=row[0],
                task_id=row[1],
                agent_role=row[2],
                provider=row[3],
                model=row[4],
                confidence=row[5],
                success=row[6],
                result_json=decode_json_object(row[7]),
                created_at=row[8],
            )
            for row in result_rows
        ]

        return TaskAuditDetail(
            task_id=task_row[0],
            task_type=task_row[1],
            symbol=task_row[2],
            status=task_row[3],
            request_json=decode_json_object(task_row[4]),
            created_at=task_row[5],
            updated_at=task_row[6],
            model_calls=model_calls,
            agent_results=agent_results,
        )

    def list_tasks(
        self,
        *,
        limit: int,
        status: str | None = None,
        symbol: str | None = None,
    ) -> TaskAuditListResponse:
        clauses: list[str] = []
        parameters: list[Any] = []

        if status:
            clauses.append("t.status = ?")
            parameters.append(status)

        if symbol:
            clauses.append("t.symbol = ?")
            parameters.append(symbol)

        where_sql = ""

        if clauses:
            where_sql = (
                "WHERE " + " AND ".join(clauses)
            )

        parameters.append(limit)

        connection = self._connect()

        try:
            rows = connection.execute(
                f"""
                SELECT
                    t.task_id,
                    t.task_type,
                    t.symbol,
                    t.status,
                    t.created_at,
                    t.updated_at,
                    (
                        SELECT COUNT(*)
                        FROM model_calls AS mc
                        WHERE mc.task_id = t.task_id
                    ) AS model_call_count,
                    (
                        SELECT COUNT(*)
                        FROM agent_results AS ar
                        WHERE ar.task_id = t.task_id
                    ) AS result_count
                FROM tasks AS t
                {where_sql}
                ORDER BY t.created_at DESC
                LIMIT ?
                """,
                parameters,
            ).fetchall()
        finally:
            connection.close()

        tasks: list[TaskAuditSummary] = []
        invalid_record_count = 0
        for row in rows:
            try:
                task = TaskAuditSummary(
                    task_id=row[0],
                    task_type=row[1],
                    symbol=row[2],
                    status=row[3],
                    created_at=row[4],
                    updated_at=row[5],
                    model_call_count=row[6],
                    result_count=row[7],
                )
            except (ValidationError, TypeError, ValueError):
                invalid_record_count += 1
                continue
            tasks.append(task)

        return TaskAuditListResponse(
            count=len(tasks),
            tasks=tasks,
            invalid_record_count=invalid_record_count,
        )
