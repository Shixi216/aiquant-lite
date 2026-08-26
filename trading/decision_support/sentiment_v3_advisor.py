"""Sentiment V3 Advisory 侧车（Phase S4A）

基于 S3C 验证的风险提醒规则，为 DecisionEngine 提供情绪面风险信号。
不改变正式 60/40 评分，仅通过置信度调节和动作降级提供风险保护。

规则 S3C_RISK_REMINDER_MEDIAN_V1：
  龙虎榜净额 < 中位数（机构净流出）
  AND 换手率 > 中位数（高换手）
  AND 缺口跟随 < 中位数（缺口不跟随）
  → 触发风险提醒

历史验证：触发后 20D 收益差 -4.35%
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ADVISOR_VERSION = "sentiment-v3-advisor-s4a-1.0.0"
RISK_RULE_VERSION = "S3C_RISK_REMINDER_MEDIAN_V1"
DEFAULT_CONFIDENCE_PENALTY = 0.15


@dataclass
class SentimentAdvisoryResult:
    """Sentiment V3 Advisory 查询结果"""
    risk_reminder: bool = False
    confidence_penalty: float = 0.0
    composite_score: float | None = None
    risk_details: dict[str, Any] = field(default_factory=dict)
    data_available: bool = False
    advisor_version: str = ADVISOR_VERSION
    rule_version: str = RISK_RULE_VERSION
    formal_weight: float = 0.0
    research_only: bool = True
    creates_trade: bool = False

    @staticmethod
    def unavailable() -> SentimentAdvisoryResult:
        """当 Sentiment V3 数据不可用时的安全默认值"""
        return SentimentAdvisoryResult(
            risk_reminder=False,
            confidence_penalty=0.0,
            data_available=False,
            risk_details={"reason": "SENTIMENT_V3_DATA_UNAVAILABLE"},
        )


class SentimentV3Advisor:
    """Sentiment V3 Advisory 侧车
    
    从实时 Shadow 快照中查询 4 个候选特征，评估风险提醒规则。
    不修改正式评分，仅输出 Advisory 结果供 DecisionEngine 使用。
    """
    
    def __init__(
        self,
        confidence_penalty: float = DEFAULT_CONFIDENCE_PENALTY,
    ) -> None:
        self.confidence_penalty = confidence_penalty
    
    def evaluate(
        self,
        dragon_tiger_net_amount: float | None = None,
        dragon_tiger_institution_net_amount: float | None = None,
        stock_turnover: float | None = None,
        gap_and_follow_through: float | None = None,
        cross_section_medians: dict[str, float] | None = None,
    ) -> SentimentAdvisoryResult:
        """评估 S3C 风险提醒规则
        
        Args:
            dragon_tiger_net_amount: 龙虎榜净额（正=净买入）
            dragon_tiger_institution_net_amount: 龙虎榜机构净额
            stock_turnover: 个股换手率
            gap_and_follow_through: 缺口跟随因子
            cross_section_medians: 截面中位数（用于比较）
        
        Returns:
            SentimentAdvisoryResult with risk_reminder flag and details
        """
        # Check data availability
        has_dragon_tiger = dragon_tiger_net_amount is not None
        has_turnover = stock_turnover is not None
        has_gap = gap_and_follow_through is not None
        
        # At minimum, need turnover and gap (dense features)
        # Dragon tiger is sparse, so risk reminder can work without it
        if not has_turnover or not has_gap:
            return SentimentAdvisoryResult.unavailable()
        
        medians = cross_section_medians or {}
        details: dict[str, Any] = {
            "rule": RISK_RULE_VERSION,
            "inputs": {
                "dragon_tiger_net_amount": dragon_tiger_net_amount,
                "stock_turnover": stock_turnover,
                "gap_and_follow_through": gap_and_follow_through,
            },
            "medians": medians,
            "checks": {},
        }
        
        # Rule: S3C_RISK_REMINDER_MEDIAN_V1
        # All 3 conditions must be met (when data available):
        # 1. Dragon tiger net < median (institutional outflow)
        # 2. Turnover > median (high turnover = overheating)
        # 3. Gap follow-through < median (gap not sustained)
        
        conditions_met = 0
        conditions_checked = 0
        
        # Condition 1: Dragon tiger outflow (only if available)
        if has_dragon_tiger and "dragon_tiger_net_amount" in medians:
            conditions_checked += 1
            dt_check = dragon_tiger_net_amount < medians["dragon_tiger_net_amount"]
            details["checks"]["dragon_tiger_outflow"] = dt_check
            if dt_check:
                conditions_met += 1
        
        # Condition 2: High turnover
        if has_turnover and "stock_turnover" in medians:
            conditions_checked += 1
            turnover_check = stock_turnover > medians["stock_turnover"]
            details["checks"]["high_turnover"] = turnover_check
            if turnover_check:
                conditions_met += 1
        
        # Condition 3: Gap not followed through
        if has_gap and "gap_and_follow_through" in medians:
            conditions_checked += 1
            gap_check = gap_and_follow_through < medians["gap_and_follow_through"]
            details["checks"]["gap_not_sustained"] = gap_check
            if gap_check:
                conditions_met += 1
        
        # Need at least 2 conditions checked for a meaningful signal
        if conditions_checked < 2:
            return SentimentAdvisoryResult(
                risk_reminder=False,
                confidence_penalty=0.0,
                data_available=True,
                risk_details={
                    **details,
                    "reason": "INSUFFICIENT_CONDITIONS_CHECKED",
                    "conditions_checked": conditions_checked,
                },
            )
        
        # Risk reminder triggers when ALL checked conditions are met
        triggered = conditions_met == conditions_checked and conditions_checked >= 2
        
        # Compute simple equal-weight composite score for reference
        composite = self._compute_composite(
            dragon_tiger_net_amount,
            stock_turnover,
            gap_and_follow_through,
        )
        
        return SentimentAdvisoryResult(
            risk_reminder=triggered,
            confidence_penalty=self.confidence_penalty if triggered else 0.0,
            composite_score=composite,
            data_available=True,
            risk_details={
                **details,
                "triggered": triggered,
                "conditions_met": conditions_met,
                "conditions_checked": conditions_checked,
            },
        )
    
    def _compute_composite(
        self,
        dragon_tiger_net: float | None,
        turnover: float | None,
        gap_follow: float | None,
    ) -> float | None:
        """计算简单等权复合分（用于参考，不影响正式评分）"""
        values = []
        # Apply feature directions (positive = bullish, negative = bearish)
        if dragon_tiger_net is not None:
            values.append(dragon_tiger_net)  # direction: +1
        if turnover is not None:
            values.append(-turnover)  # direction: -1 (high turnover = bearish)
        if gap_follow is not None:
            values.append(-gap_follow)  # direction: -1
        if not values:
            return None
        return sum(values) / len(values)


__all__ = [
    "ADVISOR_VERSION",
    "DEFAULT_CONFIDENCE_PENALTY",
    "RISK_RULE_VERSION",
    "SentimentAdvisoryResult",
    "SentimentV3Advisor",
]
