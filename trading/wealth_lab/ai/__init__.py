"""Wealth Lab AI 多智能体（复用 AIQUANT-LITE Router 的模型调用基础设施）。

不新建第二套 LLM Router；审计独立写入 wealth_lab.duckdb 的 ai_calls 表
（绝不写入股票系统 model_calls 正式记录）。
AI 只基于真实计算结果工作，输出经 Pydantic 校验；失败不影响确定性模块。
"""
from trading.wealth_lab.ai.agents import WealthResearchAgent, WealthRiskReviewer

__all__ = ["WealthResearchAgent", "WealthRiskReviewer"]
