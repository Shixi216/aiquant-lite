from __future__ import annotations

import httpx

from database.db import get_connection


BASE_URL = "http://127.0.0.1:8765"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def latest_audit_task_id() -> str:
    connection = get_connection()

    try:
        row = connection.execute(
            """
            SELECT task_id
            FROM tasks
            WHERE
                task_type = 'router_invoke'
                AND status = 'completed'
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    finally:
        connection.close()

    if row is None:
        raise RuntimeError(
            "No completed router audit task was found"
        )

    return str(row[0])


def main() -> None:
    task_id = latest_audit_task_id()

    with httpx.Client(
        base_url=BASE_URL,
        timeout=30,
        trust_env=False,
    ) as client:
        detail_response = client.get(
            f"/v1/audit/tasks/{task_id}"
        )
        detail = detail_response.json()

        print("Audit task detail:")
        print(f"status_code={detail_response.status_code}")
        print(f"task_id={detail.get('task_id')}")
        print(f"task_status={detail.get('status')}")
        print(f"symbol={detail.get('symbol')}")
        print(
            "model_call_count="
            f"{len(detail.get('model_calls', []))}"
        )
        print(
            "agent_result_count="
            f"{len(detail.get('agent_results', []))}"
        )

        require(
            detail_response.status_code == 200,
            f"Audit detail request failed: {detail}",
        )
        require(
            detail.get("task_id") == task_id,
            "Audit detail returned the wrong task",
        )
        require(
            detail.get("status") == "completed",
            "Audit task status is not completed",
        )
        require(
            bool(detail.get("model_calls")),
            "Audit detail has no model calls",
        )
        require(
            bool(detail.get("agent_results")),
            "Audit detail has no agent results",
        )

        request_json = detail.get("request_json") or {}

        require(
            request_json.get("role") == "news_processor",
            "Stored request role is incorrect",
        )

        list_response = client.get(
            "/v1/audit/tasks",
            params={
                "limit": 10,
                "status": "completed",
                "symbol": "600172.SH",
            },
        )
        task_list = list_response.json()

        print("\nAudit task list:")
        print(f"status_code={list_response.status_code}")
        print(f"count={task_list.get('count')}")

        listed_ids = {
            item.get("task_id")
            for item in task_list.get("tasks", [])
        }

        require(
            list_response.status_code == 200,
            f"Audit list request failed: {task_list}",
        )
        require(
            task_id in listed_ids,
            "Latest audit task is missing from the list",
        )

        missing_response = client.get(
            "/v1/audit/tasks/task_not_exists"
        )

        print("\nMissing task check:")
        print(f"status_code={missing_response.status_code}")

        require(
            missing_response.status_code == 404,
            "Missing audit task did not return 404",
        )

    print("\nAudit query API checks passed")


if __name__ == "__main__":
    main()