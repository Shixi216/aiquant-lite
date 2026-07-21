from __future__ import annotations

import json

from pydantic import ValidationError

from router.schemas import RiskReviewOutput, RouterInvokeRequest
from router.services.role_handler import ModelInvoker, RoleHandlerResult


RISK_CONTROLLER_SYSTEM_PROMPT = """
你是 A 股投研系统中的风险控制 Agent。你的任务是审查另一个 Agent 的输出，而不是生成新的
投资观点。重点检查：证据是否充分、事实与推断是否混淆、来源是否冲突、是否存在合规或重大
遗漏风险，以及结论是否需要修改或拒绝。

只返回一个 JSON 对象，禁止输出 Markdown 或额外解释：
{
  "decision": "approve|revise|reject",
  "assessed_risk_level": "low|medium|high|critical",
  "findings": ["风险发现"],
  "required_actions": ["必须执行的修正或人工复核动作"],
  "confidence": 0.0
}
""".strip()


def build_risk_review_prompt(
    *,
    original_role: str,
    symbol: str | None,
    risk_level: str,
    original_prompt: str,
    original_output: str,
) -> str:
    return (
        f"原始角色：{original_role}\n"
        f"证券代码：{symbol or '未提供'}\n"
        f"请求风险等级：{risk_level}\n\n"
        "原始请求：\n"
        f"{original_prompt[:12000]}\n\n"
        "待复核输出：\n"
        f"{original_output[:12000]}"
    )


def normalize_risk_review_output(content: str) -> tuple[str, RiskReviewOutput]:
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        decoded = json.loads(text)
        output = RiskReviewOutput.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise RuntimeError(f"risk_controller output validation failed: {exc}") from exc
    return output.model_dump_json(), output


class RiskControllerHandler:
    """Validate the structured DeepSeek risk-controller response."""

    async def run(
        self,
        *,
        request: RouterInvokeRequest,
        task_id: str,
        invoke_model: ModelInvoker,
    ) -> RoleHandlerResult:
        response, call_id = await invoke_model(
            prompt=request.prompt,
            system_prompt=request.system_prompt or RISK_CONTROLLER_SYSTEM_PROMPT,
            temperature=0,
            max_tokens=request.max_tokens,
        )
        normalized, output = normalize_risk_review_output(response.content)
        final_response = response.model_copy(
            update={
                "content": normalized,
                "validated": True,
                "task_id": task_id,
                "call_ids": [call_id],
            }
        )
        return RoleHandlerResult(
            response=final_response,
            result_payload=output.model_dump(mode="json"),
            confidence=output.confidence,
        )
