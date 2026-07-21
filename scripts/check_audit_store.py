from __future__ import annotations

from database.db import get_connection
from router.services.audit import (
    AuditStore,
    normalize_token_usage,
)


def cleanup(task_id: str) -> None:
    connection = get_connection()

    try:
        connection.execute(
            "DELETE FROM agent_results WHERE task_id = ?",
            [task_id],
        )
        connection.execute(
            "DELETE FROM model_calls WHERE task_id = ?",
            [task_id],
        )
        connection.execute(
            "DELETE FROM tasks WHERE task_id = ?",
            [task_id],
        )
    finally:
        connection.close()


def main() -> None:
    store = AuditStore()
    task_id: str | None = None

    usage = {
        "prompt_tokens": 788,
        "completion_tokens": 685,
        "input_tokens": 0,
        "output_tokens": 0,
    }

    input_tokens, output_tokens = (
        normalize_token_usage(usage)
    )

    print("Token normalization:")
    print(f"input_tokens={input_tokens}")
    print(f"output_tokens={output_tokens}")

    if input_tokens != 788:
        raise RuntimeError("input token normalization failed")

    if output_tokens != 685:
        raise RuntimeError("output token normalization failed")

    try:
        task_id = store.create_task(
            task_type="router_audit_test",
            symbol="600172.SH",
            request_payload={
                "role": "news_processor",
                "test": True,
            },
        )

        call_id = store.record_model_call(
            task_id=task_id,
            agent_role="news_processor",
            provider="longcat",
            model="LongCat-2.0",
            usage=usage,
            latency_ms=11405,
            success=True,
        )

        result_id = store.record_agent_result(
            task_id=task_id,
            agent_role="news_processor",
            provider="longcat",
            model="LongCat-2.0",
            confidence=0.6,
            success=True,
            result_payload={
                "validated": True,
                "attempts": 1,
                "source_class": "MEDIA_STATEMENT",
            },
        )

        store.update_task_status(
            task_id,
            "completed",
        )

        connection = get_connection()

        try:
            task_row = connection.execute(
                """
                SELECT
                    task_type,
                    symbol,
                    status
                FROM tasks
                WHERE task_id = ?
                """,
                [task_id],
            ).fetchone()

            call_row = connection.execute(
                """
                SELECT
                    agent_role,
                    provider,
                    model,
                    input_tokens,
                    output_tokens,
                    latency_ms,
                    success
                FROM model_calls
                WHERE call_id = ?
                """,
                [call_id],
            ).fetchone()

            result_row = connection.execute(
                """
                SELECT
                    agent_role,
                    provider,
                    model,
                    confidence,
                    success
                FROM agent_results
                WHERE result_id = ?
                """,
                [result_id],
            ).fetchone()
        finally:
            connection.close()

        print("\nTask record:")
        print(task_row)

        print("\nModel call record:")
        print(call_row)

        print("\nAgent result record:")
        print(result_row)

        if task_row != (
            "router_audit_test",
            "600172.SH",
            "completed",
        ):
            raise RuntimeError("task record mismatch")

        if call_row != (
            "news_processor",
            "longcat",
            "LongCat-2.0",
            788,
            685,
            11405,
            True,
        ):
            raise RuntimeError("model call record mismatch")

        if result_row != (
            "news_processor",
            "longcat",
            "LongCat-2.0",
            0.6,
            True,
        ):
            raise RuntimeError("agent result record mismatch")

        print("\nAudit store checks passed")

    finally:
        if task_id is not None:
            cleanup(task_id)
            print("Test audit records cleaned up")


if __name__ == "__main__":
    main()