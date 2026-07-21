from __future__ import annotations

import json

import httpx

from database.db import get_connection


BASE_URL = "http://127.0.0.1:8765"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    prompt = """
上游元数据：
source_name=审计测试财经媒体
source_level=media
verified=false
published_at=2026-07-17T10:00:00+08:00

原始文本：
审计测试财经媒体报道称，某券商维持黄河旋风增持评级，
目标价为15元。该消息尚未获得公司官方公告确认。

请严格按照系统规定的 JSON 结构处理。
""".strip()

    with httpx.Client(
        base_url=BASE_URL,
        timeout=120,
        trust_env=False,
    ) as client:
        response = client.post(
            "/v1/invoke",
            json={
                "role": "news_processor",
                "symbol": "600172.SH",
                "prompt": prompt,
                "temperature": 0,
                "max_tokens": 1200,
            },
        )

    data = response.json()

    print("Router response:")
    print(f"status_code={response.status_code}")

    require(
        response.status_code == 200,
        f"Router invocation failed: {data}",
    )

    task_id = data.get("task_id")
    call_ids = data.get("call_ids")
    attempts = data.get("attempts")

    print(f"task_id={task_id}")
    print(f"call_ids={call_ids}")
    print(f"attempts={attempts}")
    print(f"validated={data.get('validated')}")

    require(
        isinstance(task_id, str) and bool(task_id),
        "response does not contain task_id",
    )
    require(
        isinstance(call_ids, list),
        "response does not contain call_ids",
    )
    require(
        len(call_ids) == attempts,
        "call_ids count does not match attempts",
    )
    require(
        data.get("validated") is True,
        "Router output was not validated",
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

        call_rows = connection.execute(
            """
            SELECT
                call_id,
                agent_role,
                provider,
                model,
                input_tokens,
                output_tokens,
                latency_ms,
                success
            FROM model_calls
            WHERE task_id = ?
            ORDER BY created_at
            """,
            [task_id],
        ).fetchall()

        result_row = connection.execute(
            """
            SELECT
                agent_role,
                provider,
                model,
                confidence,
                success,
                result_json
            FROM agent_results
            WHERE task_id = ?
            """,
            [task_id],
        ).fetchone()

    finally:
        connection.close()

    print("\nTask audit:")
    print(task_row)

    print("\nModel call audits:")
    for row in call_rows:
        print(row)

    print("\nAgent result audit:")
    print(result_row[:5] if result_row else None)

    require(
        task_row == (
            "router_invoke",
            "600172.SH",
            "completed",
        ),
        "task audit record mismatch",
    )

    require(
        len(call_rows) == attempts,
        "model call audit count mismatch",
    )

    require(
        all(row[7] is True for row in call_rows),
        "a model call audit is marked as failed",
    )

    require(
        all(row[4] is not None for row in call_rows),
        "input token usage was not stored",
    )

    require(
        all(row[5] is not None for row in call_rows),
        "output token usage was not stored",
    )

    require(
        result_row is not None,
        "agent result audit is missing",
    )

    require(
        result_row[4] is True,
        "agent result audit is not successful",
    )

    result_payload = result_row[5]

    if isinstance(result_payload, str):
        result_payload = json.loads(result_payload)

    require(
        isinstance(result_payload, dict),
        "agent result JSON is invalid",
    )

    require(
        result_payload.get("validated") is True,
        "validated status was not persisted",
    )

    print("\nRouter audit integration checks passed")
    print("The audit record was retained in DuckDB.")


if __name__ == "__main__":
    main()