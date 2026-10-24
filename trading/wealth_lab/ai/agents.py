"""AI 多资产研究/风险复核 Agent（真实模型调用，独立审计，结构化输出）。

复用 router.providers 的 OpenAI 兼容调用（DeepSeek 优先）。
多资产专用提示词（不复用股票 Agent 提示词）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field, field_validator

from trading.wealth_lab.config import WEALTH_LAB_DB_PATH

_TZ = timezone(timedelta(hours=8))
PROMPT_VERSION = "wealth-research-v1"

_AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS wealth_ai_calls (
    call_id VARCHAR PRIMARY KEY,
    agent VARCHAR NOT NULL,
    model VARCHAR,
    provider VARCHAR,
    prompt_version VARCHAR,
    prompt_sha256 VARCHAR,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER,
    success BOOLEAN,
    error_type VARCHAR,
    created_at TIMESTAMPTZ
);
"""


def _audit(call_id: str, agent: str, model: str | None, provider: str | None,
           prompt: str, meta: dict) -> None:
    with duckdb.connect(str(WEALTH_LAB_DB_PATH)) as con:
        con.execute(_AUDIT_SCHEMA)
        con.execute(
            "INSERT INTO wealth_ai_calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [call_id, agent, model, provider, PROMPT_VERSION,
             hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
             meta.get("input_tokens"), meta.get("output_tokens"),
             meta.get("latency_ms"), meta.get("success", False),
             meta.get("error_type"), datetime.now(_TZ)],
        )


class AiResearchOutput(BaseModel):
    """契约 v2 AiResearchOutput 的 Pydantic 校验。"""
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=20, max_length=2000)
    strategy_differences: list[str] = Field(default_factory=list, max_length=10)
    key_risks: list[str] = Field(min_length=1, max_length=10)
    counter_evidence: list[str] = Field(default_factory=list, max_length=10)
    data_gaps: list[str] = Field(default_factory=list, max_length=10)


class AiRiskReviewOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str = Field(pattern="^(SUPPORTED|NEEDS_MORE_EVIDENCE|CONTRADICTED)$")
    unchecked_claims: list[str] = Field(default_factory=list, max_length=10)
    hallucination_flags: list[str] = Field(default_factory=list, max_length=10)
    human_review_required: bool = False
    notes: str = Field(default="", max_length=2000)


def _provider():
    """复用项目 Router 的 provider：DeepSeek 优先，Qwen 兜底。"""
    from router.providers.deepseek import DeepSeekProvider
    from router.config import router_settings
    if router_settings.deepseek_ready:
        return DeepSeekProvider(), "deepseek"
    from router.providers.qwen import QwenProvider
    return QwenProvider(), "qwen"


def _parse_json_block(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model output contains no JSON object")
    return json.loads(text[start: end + 1])


class WealthResearchAgent:
    """多资产研究员：只基于传入的真实计算结果做研究摘要。"""

    agent = "WealthResearchAgent"

    async def run(self, facts: dict[str, Any]) -> dict:
        provider, provider_name = _provider()
        call_id = "call_wl_" + uuid.uuid4().hex[:16]
        facts_json = json.dumps(facts, ensure_ascii=False, indent=1)[:12000]
        system = (
            "你是AIQUANT-LITE财富管理实验室的多资产研究员。你只基于用户提供的真实计算结果工作，"
            "严禁编造任何数字、权重或行情；数字必须能在给定事实中找到。"
            "用中文输出一个JSON对象，字段：summary(字符串), strategy_differences(字符串数组), "
            "key_risks(字符串数组), counter_evidence(字符串数组), data_gaps(字符串数组)。"
            "不要输出JSON以外的内容。这是研究辅助，不构成投资建议。"
        )
        prompt = (
            f"已知事实（全部来自确定性计算）：\n{facts_json}\n\n"
            "请输出结构化研究摘要。要求：对比策略差异、指出关键风险与反面证据、列出数据不足项；"
            "所有数字必须来自上述事实。"
        )
        meta: dict = {}
        try:
            import time
            t0 = time.perf_counter()
            response = await provider.invoke(
                role="wealth_research", prompt=prompt, system_prompt=system,
                temperature=0.2, max_tokens=1600,
            )
            meta = {
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "input_tokens": getattr(response, "input_tokens", None),
                "output_tokens": getattr(response, "output_tokens", None),
                "success": True,
            }
            payload = _parse_json_block(getattr(response, "content", "") or "")
            validated = AiResearchOutput.model_validate(payload)
            _audit(call_id, self.agent, getattr(response, "model", provider_name),
                   provider_name, prompt, meta)
            return {
                "research_id": "wlrs_" + uuid.uuid4().hex[:12],
                "model": getattr(response, "model", provider_name),
                "model_call_ids": [call_id],
                "prompt_version": PROMPT_VERSION,
                "schema_valid": True,
                **validated.model_dump(),
            }
        except Exception as exc:
            _audit(call_id, self.agent, None, provider_name, prompt,
                   {"success": False, "error_type": type(exc).__name__})
            raise


class WealthRiskReviewer:
    """风险审查员：核查研究结论是否有证据、识别幻觉。"""

    agent = "WealthRiskReviewer"

    async def run(self, research: dict, computed_numbers: dict[str, float]) -> dict:
        provider, provider_name = _provider()
        call_id = "call_wl_" + uuid.uuid4().hex[:16]
        numbers_json = json.dumps(computed_numbers, ensure_ascii=False)
        system = (
            "你是AIQUANT-LITE财富管理实验室的风险审查员。你的职责是核查研究结论是否与给定的"
            "计算数字一致、识别可能的幻觉表述、指出需要人工复核的事项。"
            "输出JSON：{verdict: SUPPORTED|NEEDS_MORE_EVIDENCE|CONTRADICTED, "
            "unchecked_claims: [..], hallucination_flags: [..], human_review_required: bool, notes: str}。"
            "不要输出JSON以外的内容。"
        )
        prompt = (
            f"研究结果：\n{json.dumps(research, ensure_ascii=False)[:6000]}\n\n"
            f"计算数字（唯一可信来源）：\n{numbers_json}\n\n请审查。"
        )
        try:
            import time
            t0 = time.perf_counter()
            response = await provider.invoke(
                role="wealth_risk_review", prompt=prompt, system_prompt=system,
                temperature=0.1, max_tokens=900,
            )
            meta = {
                "latency_ms": int((time.perf_counter() - t0) * 1000),
                "success": True,
            }
            payload = _parse_json_block(getattr(response, "content", "") or "")
            validated = AiRiskReviewOutput.model_validate(payload)
            _audit(call_id, self.agent, getattr(response, "model", provider_name),
                   provider_name, prompt, meta)
            return {
                "review_id": "wlrv_" + uuid.uuid4().hex[:12],
                "model": getattr(response, "model", provider_name),
                "model_call_ids": [call_id],
                **validated.model_dump(),
            }
        except Exception as exc:
            _audit(call_id, self.agent, None, provider_name, prompt,
                   {"success": False, "error_type": type(exc).__name__})
            raise


def run_research_sync(facts: dict) -> dict:
    return asyncio.run(WealthResearchAgent().run(facts))


def run_risk_review_sync(research: dict, numbers: dict[str, float]) -> dict:
    return asyncio.run(WealthRiskReviewer().run(research, numbers))
