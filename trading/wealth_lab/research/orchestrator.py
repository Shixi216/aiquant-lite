"""三模型调用与结构化输出（Planner 计划 Pydantic + 白名单校验）。"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator

from trading.wealth_lab.research.audit import log_call
from trading.wealth_lab.research.roles import ROLE_SPECS

_TZ = timezone(timedelta(hours=8))
CALL_TIMEOUT_SECONDS = 300
PLANNER_PROMPT_VERSION = "wealth-planner-v1"
RESEARCHER_PROMPT_VERSION = "wealth-researcher-qwen-v1"
REVIEWER_PROMPT_VERSION = "wealth-reviewer-pro-v1"


class ResearchPlan(BaseModel):
    """DeepSeek Flash Planner 的结构化计划（白名单校验后才能进入实验）。"""
    model_config = ConfigDict(extra="forbid")

    research_goal: str = Field(min_length=4, max_length=500)
    universe_id: str
    strategy_candidates: list[str] = Field(min_length=1, max_length=5)
    start_date: date
    end_date: date
    benchmark: str = Field(default="EQUAL_WEIGHT")
    experiment_params: dict = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list, max_length=8)
    hypotheses: list[str] = Field(default_factory=list, max_length=8)
    required_evidence: list[str] = Field(default_factory=list, max_length=10)
    limitations: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("end_date")
    @classmethod
    def window_valid(cls, value: date, info):
        start = info.data.get("start_date")
        if start and value <= start:
            raise ValueError("end_date must be after start_date")
        return value


def validate_plan(plan: ResearchPlan, universes: dict, strategies: set) -> list[str]:
    """白名单校验：universe 必须存在、策略必须属于 S0-S8 注册表、区间不得晚于数据末日。

    数量超限（>3）做确定性截断（VALIDATION 环节职责，不依赖模型自觉、不重试模型）；
    非法 universe/策略/日期一律拒绝。
    """
    errors = []
    if plan.universe_id not in universes:
        errors.append(f"universe_id 不存在: {plan.universe_id}")
    unknown = [s for s in plan.strategy_candidates if s not in strategies]
    if unknown:
        errors.append(f"策略不在 S0-S8 注册表: {unknown}")
    if plan.end_date > date(2026, 10, 23):
        errors.append("end_date 晚于数据池最后一日 2026-10-23")
    if len(plan.strategy_candidates) > 3:
        plan.strategy_candidates = plan.strategy_candidates[:3]
        errors_note = f"策略候选超 3 个，确定性截断为前 3: {plan.strategy_candidates}"
        plan.limitations = list(plan.limitations or []) + [errors_note]
    return errors


async def _chat(provider: str, model: str, system: str, prompt: str,
                temperature: float, max_tokens: int) -> dict:
    """按角色显式 model_id 调用（不猜测、不冒充）。"""
    from router.config import router_settings
    from router.providers.openai_compatible import invoke_openai_compatible_chat
    if provider == "qwen":
        secret, base = router_settings.qwen_api_key, router_settings.qwen_base_url
    else:
        secret, base = router_settings.deepseek_api_key, router_settings.deepseek_base_url
    if secret is None or not base:
        raise RuntimeError(f"MODEL_UNAVAILABLE: {provider} 凭证未配置")
    response = await invoke_openai_compatible_chat(
        provider_name=provider, provider_label=provider.title(), secret=secret,
        base_url=base, model=model, role="wealth_research", prompt=prompt,
        system_prompt=system, temperature=temperature, max_tokens=max_tokens,
        extra_body=({"thinking": {"type": "disabled"}} if provider == "deepseek" else None),
    )
    return {
        "content": getattr(response, "content", "") or "",
        "model": getattr(response, "model", model),
        "input_tokens": getattr(response, "input_tokens", None),
        "output_tokens": getattr(response, "output_tokens", None),
    }


def _parse_json(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output contains no JSON object")
    return json.loads(text[start: end + 1])


async def call_with_audit(research_run_id: str, role: str, system: str, prompt: str,
                          *, temperature: float, max_tokens: int,
                          backtest_id: str | None = None) -> dict:
    """统一入口：角色映射→真实调用→审计。模型不可用则抛 MODEL_UNAVAILABLE。"""
    spec = ROLE_SPECS[role]
    if not spec.available or not spec.actual_model_id:
        log_call(research_run_id, role, spec.display_model_name, None, spec.provider,
                 "n/a", datetime.now(_TZ), status="MODEL_UNAVAILABLE",
                 error_reason=spec.note or "model not configured",
                 associated_backtest_id=backtest_id)
        raise RuntimeError(f"MODEL_UNAVAILABLE:{role}:{spec.display_model_name}")
    started = datetime.now(_TZ)
    t0 = time.perf_counter()
    try:
        result = await asyncio.wait_for(
            _chat(spec.provider, spec.actual_model_id, system, prompt,
                  temperature, max_tokens),
            timeout=CALL_TIMEOUT_SECONDS,
        )
        call_id = log_call(research_run_id, role, spec.display_model_name,
                           spec.actual_model_id, spec.provider,
                           {"PLANNER": PLANNER_PROMPT_VERSION,
                            "RESEARCHER": RESEARCHER_PROMPT_VERSION,
                            "REVIEWER": REVIEWER_PROMPT_VERSION}[role],
                           started, latency_ms=int((time.perf_counter() - t0) * 1000),
                           input_tokens=result.get("input_tokens"),
                           output_tokens=result.get("output_tokens"),
                           status="OK", associated_backtest_id=backtest_id,
                           evidence_ids=[backtest_id] if backtest_id else [])
        return {"call_id": call_id, **result}
    except asyncio.TimeoutError:
        log_call(research_run_id, role, spec.display_model_name, spec.actual_model_id,
                 spec.provider, "n/a", started, status="TIMEOUT",
                 error_reason=f"exceeded {CALL_TIMEOUT_SECONDS}s",
                 associated_backtest_id=backtest_id)
        raise RuntimeError(f"MODEL_TIMEOUT:{role}")
    except Exception as exc:
        log_call(research_run_id, role, spec.display_model_name, spec.actual_model_id,
                 spec.provider, "n/a", started, status="FAILED",
                 error_reason=f"{type(exc).__name__}: {str(exc)[:200]}",
                 associated_backtest_id=backtest_id)
        raise


async def run_planner(research_run_id: str, goal: str, universe_options: list[str],
                      strategy_options: list[str]) -> ResearchPlan:
    system = (
        "你是AIQUANT-LITE财富管理实验室的研究规划师。你只输出一个JSON对象，不输出其他内容。"
        "字段：research_goal, universe_id, strategy_candidates(数组,1-3个), start_date(YYYY-MM-DD), "
        "end_date, benchmark, experiment_params(对象), constraints(数组), hypotheses(数组), "
        "required_evidence(数组), limitations(数组)。数据池截止2026-10-23。"
        "你只能提出计划，不能执行任何实验。"
    )
    prompt = (
        f"用户研究目标：{goal}\n\n"
        f"可用资产池：{universe_options}\n"
        f"可用策略（S0-S8）：{strategy_options}\n\n"
        "请给出一个可执行的研究计划（严格遵守字段与日期约束）。"
    )
    result = await call_with_audit(research_run_id, "PLANNER", system, prompt,
                                   temperature=0.2, max_tokens=1200)
    payload = _parse_json(result["content"])
    return ResearchPlan.model_validate(payload)


RESEARCHER_SYSTEM = (
    "你是AIQUANT-LITE财富管理实验室的金融研究员（Qwen）。你只基于用户提供的真实计算结果工作，"
    "严禁编造任何数字。所有数字必须能在给定事实中找到。输出JSON："
    "{summary, strategy_differences:[..], key_risks:[..], counter_evidence:[..], data_gaps:[..]}。"
    "不输出JSON以外的内容。这是研究辅助，不构成投资建议。"
)

REVIEWER_SYSTEM = (
    "你是AIQUANT-LITE财富管理实验室的独立审阅人（DeepSeek Pro）。你独立审查研究结论："
    "核对证据与数字、识别逻辑矛盾、检查过拟合/样本外失效/幸存者偏差、检查流动性与集中度及"
    "QDII汇率等风险，并对照给定的确定性风控结果。你只能审查，无权修改收益、仓位或解除VETO。"
    "输出JSON：{verdict: SUPPORTED|NEEDS_MORE_EVIDENCE|CONTRADICTED, "
    "unchecked_claims:[..], hallucination_flags:[..], human_review_required: bool, notes}。"
    "不输出JSON以外的内容。"
)
