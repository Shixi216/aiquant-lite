from __future__ import annotations

import json

from pydantic import ValidationError

from router.schemas import RouterInvokeRequest, ResearchSynthesisOutput
from router.services.role_handler import ModelInvoker, RoleHandlerResult


RESEARCH_SYSTEM_PROMPT = """
你是A股研究证据整理Agent，只能依据用户提供的本地点时研究摘要工作。

硬性边界：
1. 这是RESEARCH，不是正式DECISION，不得输出BUY、SELL或HOLD；
2. 不得补造缺失的基本面、情绪、政策或资金数据；
3. 必须明确列出缺失数据和风险；
4. 不得改变正式技术面60%与基本面40%的权重；
5. 不得改变任何硬风险VETO；
6. 只输出一个JSON对象，不得输出Markdown或额外解释。

输出结构：
{
  "symbol": "股票代码",
  "summary": "基于现有证据的研究摘要",
  "factor_observations": [
    {
      "factor": "TECHNICAL|FUNDAMENTAL|SENTIMENT|POLICY_NEWS|CAPITAL_FLOW",
      "status": "AVAILABLE|MISSING|STALE|PARTIAL",
      "observation": "不超过1000字的客观观察"
    }
  ],
  "missing_data": ["缺失项"],
  "risk_flags": ["风险标记"],
  "confidence": 0.0,
  "not_trade_recommendation": true
}
""".strip()


def normalize_research_output(
    content: str,
    *,
    expected_symbol: str | None,
) -> ResearchSynthesisOutput:
    stripped = content.strip()
    if not stripped or "```" in stripped:
        raise RuntimeError("RESEARCH_OUTPUT_INVALID")
    try:
        output = ResearchSynthesisOutput.model_validate_json(stripped)
    except ValidationError as exc:
        raise RuntimeError("RESEARCH_OUTPUT_INVALID") from exc
    if expected_symbol and output.symbol != expected_symbol:
        raise RuntimeError("RESEARCH_SYMBOL_MISMATCH")
    if output.not_trade_recommendation is not True:
        raise RuntimeError("RESEARCH_BOUNDARY_VIOLATION")
    forbidden = {"BUY", "SELL", "HOLD"}
    if forbidden.intersection(flag.upper() for flag in output.risk_flags):
        raise RuntimeError("RESEARCH_BOUNDARY_VIOLATION")
    return output


class ResearchSynthesizerHandler:
    async def run(
        self,
        *,
        request: RouterInvokeRequest,
        task_id: str,
        invoke_model: ModelInvoker,
    ) -> RoleHandlerResult:
        response, call_id = await invoke_model(
            prompt=request.prompt,
            system_prompt=request.system_prompt or RESEARCH_SYSTEM_PROMPT,
            temperature=min(request.temperature, 0.2),
            max_tokens=request.max_tokens,
        )
        output = normalize_research_output(
            response.content,
            expected_symbol=request.symbol,
        )
        normalized = json.dumps(
            output.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return RoleHandlerResult(
            response=response.model_copy(
                update={
                    "content": normalized,
                    "validated": True,
                    "task_id": task_id,
                    "call_ids": [call_id],
                }
            ),
            result_payload=output.model_dump(mode="json"),
            confidence=output.confidence,
        )


__all__ = [
    "RESEARCH_SYSTEM_PROMPT",
    "ResearchSynthesizerHandler",
    "normalize_research_output",
]
