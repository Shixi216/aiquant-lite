from __future__ import annotations

from datetime import date
from typing import Any

import httpx


ROUTER_URL = "http://127.0.0.1:8765"
SYMBOL = "600172.SH"
ANNOUNCEMENT_ID = "1225422790"

CLAIMS = [
    (
        "公司预计2026年半年度归属于母公司"
        "所有者的净利润为-22,000万元。"
    ),
    "本期业绩预告数据已经会计师审计。",
    (
        "该公告披露了公司2026年全年"
        "营业收入的具体金额。"
    ),
]

EXPECTED_VERDICTS = [
    "SUPPORTED",
    "CONTRADICTED",
    "INSUFFICIENT_EVIDENCE",
]


def require(
    condition: bool,
    message: str,
) -> None:
    if not condition:
        raise RuntimeError(message)


def require_dict(
    value: Any,
    message: str,
) -> dict[str, Any]:
    require(
        isinstance(value, dict),
        message,
    )
    return value


def require_list(
    value: Any,
    message: str,
) -> list[Any]:
    require(
        isinstance(value, list),
        message,
    )
    return value


def main() -> None:
    request_payload = {
        "symbol": SYMBOL,
        "announcement_id": ANNOUNCEMENT_ID,
        "start_date": "2026-01-01",
        "end_date": "2026-01-02",
        "claims": CLAIMS,
        "max_tokens": 5000,
    }

    missing_selector_payload = {
        key: value
        for key, value in request_payload.items()
        if key != "announcement_id"
    }

    with httpx.Client(
        base_url=ROUTER_URL,
        timeout=300,
        trust_env=False,
    ) as client:
        response = client.post(
            "/v1/pipelines/"
            "announcement-verification",
            json=request_payload,
        )

        print("Announcement pipeline API:")
        print(f"status_code={response.status_code}")

        if response.status_code != 200:
            print(
                "response="
                f"{response.text[:2000]}"
            )
            response.raise_for_status()

        payload = require_dict(
            response.json(),
            "Pipeline 响应不是 JSON 对象",
        )

        missing_selector = client.post(
            "/v1/pipelines/"
            "announcement-verification",
            json=missing_selector_payload,
        )

        task_id = str(payload.get("task_id") or "")

        audit_response = client.get(
            f"/v1/audit/tasks/{task_id}"
        )
        audit_response.raise_for_status()

        audit = require_dict(
            audit_response.json(),
            "审计响应不是 JSON 对象",
        )

    output = require_dict(
        payload.get("output"),
        "Pipeline 缺少结构化 output",
    )

    claims = require_list(
        output.get("claims"),
        "Pipeline output 缺少 claims",
    )

    call_ids = require_list(
        payload.get("call_ids"),
        "Pipeline 缺少 call_ids",
    )

    model_calls = require_list(
        audit.get("model_calls"),
        "审计记录缺少 model_calls",
    )

    agent_results = require_list(
        audit.get("agent_results"),
        "审计记录缺少 agent_results",
    )

    verdicts = [
        str(
            require_dict(
                claim,
                "claim 不是 JSON 对象",
            ).get("verdict")
        )
        for claim in claims
    ]

    audit_call_ids = [
        str(
            require_dict(
                call,
                "model_call 不是 JSON 对象",
            ).get("call_id")
        )
        for call in model_calls
    ]

    announcement_date = date.fromisoformat(
        str(payload.get("announcement_date"))
    )
    start_date = date.fromisoformat(
        request_payload["start_date"]
    )
    end_date = date.fromisoformat(
        request_payload["end_date"]
    )

    date_window_excludes_announcement = not (
        start_date
        <= announcement_date
        <= end_date
    )

    print(f"task_id={task_id}")
    print(
        "announcement_id="
        f"{payload.get('announcement_id')}"
    )
    print(
        "announcement_date="
        f"{payload.get('announcement_date')}"
    )
    print(
        "date_window_excludes_announcement="
        f"{date_window_excludes_announcement}"
    )
    print(
        "missing_selector_status="
        f"{missing_selector.status_code}"
    )
    print(f"validated={payload.get('validated')}")
    print(f"attempts={payload.get('attempts')}")
    print(f"provider={payload.get('provider')}")
    print(f"model={payload.get('model')}")
    print(
        "latency_ms="
        f"{payload.get('latency_ms')}"
    )
    print(f"call_ids={call_ids}")
    print(f"verdicts={verdicts}")

    print("\nAudit chain:")
    print(
        "task_type="
        f"{audit.get('task_type')}"
    )
    print(f"task_status={audit.get('status')}")
    print(
        "model_call_count="
        f"{len(model_calls)}"
    )
    print(
        "agent_result_count="
        f"{len(agent_results)}"
    )

    require(
        bool(task_id),
        "Pipeline 没有返回 task_id",
    )
    require(
        payload.get("announcement_id")
        == ANNOUNCEMENT_ID,
        "Pipeline 返回了错误公告",
    )
    require(
        date_window_excludes_announcement,
        "测试日期范围没有排除目标公告",
    )
    require(
        missing_selector.status_code == 422,
        "缺少公告选择器时没有返回 422",
    )
    require(
        payload.get("validated") is True,
        "Pipeline 没有通过结构校验",
    )
    require(
        payload.get("provider") == "qwen",
        "Pipeline Provider 错误",
    )
    require(
        payload.get("model") == "qwen3.7-plus",
        "Pipeline 模型错误",
    )
    require(
        verdicts == EXPECTED_VERDICTS,
        "公告核验结论错误",
    )
    require(
        audit.get("task_type")
        == "announcement_verification_pipeline",
        "审计任务类型错误",
    )
    require(
        audit.get("status") == "completed",
        "审计任务未完成",
    )
    require(
        len(model_calls)
        == int(payload.get("attempts") or 0),
        "模型调用次数与 attempts 不一致",
    )
    require(
        len(agent_results) == 1,
        "Agent 结果记录数量异常",
    )
    require(
        set(call_ids) == set(audit_call_ids),
        "响应 call_ids 与审计记录不一致",
    )

    print(
        "\nAnnouncement pipeline API "
        "and audit checks passed"
    )


if __name__ == "__main__":
    main()