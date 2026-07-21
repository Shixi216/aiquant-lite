"""Run one redacted end-to-end high-risk Router and audit-chain check."""

from __future__ import annotations

import asyncio
import json

from router.schemas import BudgetTier, RiskLevel, RouterInvokeRequest
from router.services.audit_query import AuditQueryStore
from router.services.invocation import RouterInvocationService


SYNTHETIC_PROMPT = """
处理以下虚构测试材料，不要补充外部信息：
record_id=test-001
source_level=official
verified=true
source_name=测试交易所
event_time=2026-07-21T15:00:00+08:00
content=示例公司发布测试公告，说明本材料只用于系统连通性验证，不包含真实市场事件。
""".strip()


async def run() -> dict[str, object]:
    response = await RouterInvocationService().invoke(
        RouterInvokeRequest(
            role="news_processor",
            symbol="TEST000",
            prompt=SYNTHETIC_PROMPT,
            temperature=0,
            max_tokens=1024,
            risk_level=RiskLevel.HIGH,
            budget_tier=BudgetTier.STANDARD,
        )
    )
    if response.task_id is None:
        raise RuntimeError("Router response did not include task_id")
    audit = AuditQueryStore().get_task(response.task_id)
    if audit is None:
        raise RuntimeError("Router audit task was not persisted")
    return {
        "task_id": response.task_id,
        "primary_provider": response.provider,
        "primary_model": response.model,
        "primary_validated": response.validated,
        "routing_risk": response.routing.risk_level if response.routing else None,
        "human_review_required": (
            response.routing.human_review_required if response.routing else None
        ),
        "risk_review_status": response.risk_review.status if response.risk_review else None,
        "risk_review_provider": response.risk_review.provider if response.risk_review else None,
        "physical_call_count": len(audit.model_calls),
        "agent_result_count": len(audit.agent_results),
        "audit_status": audit.status,
        "secrets_printed": False,
    }


def main() -> int:
    try:
        payload = asyncio.run(run())
    except Exception as exc:
        print(json.dumps({"status": "error", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps({"status": "ok", **payload}, ensure_ascii=False, indent=2, default=str))
    if payload["risk_review_status"] != "completed" or not payload["primary_validated"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
