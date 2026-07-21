from __future__ import annotations

from datetime import date

import httpx


BASE_URL = "http://127.0.0.1:8765"
SYMBOL = "600172.SH"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    today = date.today()
    start_date = date(
        today.year,
        1,
        1,
    ).isoformat()

    with httpx.Client(
        base_url=BASE_URL,
        timeout=240,
        trust_env=False,
    ) as client:
        response = client.post(
            "/v1/pipelines/news-analysis",
            json={
                "symbol": SYMBOL,
                "start_date": start_date,
                "end_date": today.isoformat(),
                "finance_news_limit": 3,
                "announcement_limit": 3,
                "temperature": 0,
                "max_tokens": 6000,
            },
        )

        data = response.json()

        print("News pipeline response:")
        print(f"status_code={response.status_code}")

        require(
            response.status_code == 200,
            f"News pipeline failed: {data}",
        )

        print(f"symbol={data.get('symbol')}")
        print(
            "finance_news_count="
            f"{data.get('finance_news_count')}"
        )
        print(
            "announcement_count="
            f"{data.get('announcement_count')}"
        )
        print(
            "source_record_count="
            f"{data.get('source_record_count')}"
        )
        print(f"task_id={data.get('task_id')}")
        print(f"call_ids={data.get('call_ids')}")
        print(f"attempts={data.get('attempts')}")
        print(f"validated={data.get('validated')}")
        print(f"provider={data.get('provider')}")
        print(f"model={data.get('model')}")
        print(f"latency_ms={data.get('latency_ms')}")

        require(
            data.get("symbol") == SYMBOL,
            "Pipeline returned the wrong symbol",
        )
        require(
            data.get("finance_news_count") == 3,
            "Finance news count is incorrect",
        )
        require(
            data.get("announcement_count") == 3,
            "Announcement count is incorrect",
        )
        require(
            data.get("source_record_count") == 6,
            "Source record count is incorrect",
        )
        require(
            len(data.get("selected_record_ids", [])) == 6,
            "Selected record ID count is incorrect",
        )
        require(
            data.get("validated") is True,
            "Pipeline output was not validated",
        )

        task_id = data.get("task_id")
        attempts = data.get("attempts")
        call_ids = data.get("call_ids")

        require(
            isinstance(task_id, str) and bool(task_id),
            "Pipeline response has no task_id",
        )
        require(
            isinstance(call_ids, list),
            "Pipeline response has no call_ids",
        )
        require(
            len(call_ids) == attempts,
            "Call count does not match attempts",
        )

        output = data.get("output")

        require(
            isinstance(output, dict),
            "Pipeline output is not an object",
        )

        items = output.get("items")

        require(
            isinstance(items, list) and bool(items),
            "Pipeline output has no news items",
        )

        valid_classes = {
            "FACT",
            "MEDIA_STATEMENT",
            "SPECULATION",
        }

        source_classes = {
            item.get("source_class")
            for item in items
            if isinstance(item, dict)
        }

        require(
            source_classes.issubset(valid_classes),
            "Pipeline returned an invalid source class",
        )

        print(
            "output_item_count="
            f"{len(items)}"
        )
        print(
            "source_classes="
            f"{sorted(source_classes)}"
        )

        audit_response = client.get(
            f"/v1/audit/tasks/{task_id}"
        )
        audit = audit_response.json()

        print("\nPipeline audit:")
        print(
            f"status_code="
            f"{audit_response.status_code}"
        )
        print(f"task_status={audit.get('status')}")
        print(
            "model_call_count="
            f"{len(audit.get('model_calls', []))}"
        )
        print(
            "agent_result_count="
            f"{len(audit.get('agent_results', []))}"
        )

        require(
            audit_response.status_code == 200,
            f"Audit query failed: {audit}",
        )
        require(
            audit.get("status") == "completed",
            "Pipeline audit is not completed",
        )
        require(
            len(audit.get("model_calls", []))
            == attempts,
            "Audit model call count is incorrect",
        )
        require(
            len(audit.get("agent_results", [])) == 1,
            "Audit agent result count is incorrect",
        )

    print("\nNews analysis pipeline checks passed")


if __name__ == "__main__":
    main()