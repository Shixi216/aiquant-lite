from __future__ import annotations

from trading.schemas import AnalysisMode


def resolve_analysis_mode(
    user_text: str,
    *,
    explicit_formal_decision: bool = False,
) -> AnalysisMode:
    """Deterministically route chat intent without invoking an LLM."""

    normalized = "".join(user_text.casefold().split())
    if explicit_formal_decision:
        return AnalysisMode.DECISION
    if any(
        marker in normalized
        for marker in (
            "详细分析",
            "深度研究",
            "研究报告",
            "五维研究",
        )
    ):
        return AnalysisMode.RESEARCH
    return AnalysisMode.SCREENING


__all__ = ["resolve_analysis_mode"]
