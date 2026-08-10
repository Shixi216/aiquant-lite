"""统一决策响应 DTO DecisionResponse（阶段1.10）

包含：任务上下文/数据状态/动作/评分/持仓/交易指导/风险/下一步
价格区间先保留结构，完整 TradePlan 下一阶段实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trading.decision_support.action import Action
from trading.decision_support.data_status import DataStatus
from trading.decision_support.task_context import TaskContext


@dataclass
class PriceZone:
    """价格区间（结构占位，完整TradePlan下阶段）"""
    entry_zone: list = field(default_factory=list)        # 建仓区间 [low, high]
    preferred_zone: list = field(default_factory=list)     # 优选建仓区间 [low, high]
    add_zone: list = field(default_factory=list)          # 加仓区间
    reduce_zone: list = field(default_factory=list)       # 减仓区间
    take_profit_zone: list = field(default_factory=list)  # 止盈区间
    stop_loss_price: Optional[float] = None               # 止损价
    stop_loss_condition: str = ""
    invalidation_condition: str = ""
    expected_holding_period: str = ""


@dataclass
class DecisionResponse:
    # 任务上下文
    task_context: Optional[TaskContext] = None

    # 数据状态
    data_status: DataStatus = DataStatus.FRESH
    trade_date: str = ""
    collected_at: str = ""
    data_cutoff: str = ""
    snapshot_id: str = ""
    coverage_ratio: float = 0.0

    # 明确结论
    action: Action = Action.WAIT
    action_zh: str = "等待"
    action_strength: str = "normal"
    allow_new_position: bool = False
    allow_add_position: bool = False
    recommend_reduce: bool = False
    recommend_exit: bool = False
    veto_triggered: bool = False

    # 评分
    formal_score: float = 0.0
    enhanced_score: Optional[float] = None
    confidence: float = 0.5
    consistency_status: str = ""       # CONSISTENT / INCONSISTENT / NA
    inconsistency_reason: str = ""

    # 仓位建议
    current_position_ratio: float = 0.0
    target_position_ratio: float = 0.0
    position_change_ratio: float = 0.0
    recommended_batches: int = 1

    # 交易区间（结构占位）
    price_zones: PriceZone = field(default_factory=PriceZone)

    # 依据与风险
    supporting_reasons: list[str] = field(default_factory=list)
    major_risks: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)

    # 下一步
    next_action: str = ""
    # 执行状态（独立于正式动作）
    execution_status: str = ""  # 立即执行/等待回调/等待突破/等待确认/已失效
    execution_status_reason: str = ""  # 状态原因说明
    # 冻结价格区间（首次决策时生成，后续不可修改）
    frozen_entry_zone: list = field(default_factory=list)
    frozen_preferred_zone: list = field(default_factory=list)
    frozen_stop_loss_price: float = 0.0
    zone_version: str = "V1"

    def to_dict(self) -> dict:
        action_value = self.action.value if hasattr(self.action, "value") else str(self.action)
        return {
            "data_status": self.data_status.value if hasattr(self.data_status, "value") else str(self.data_status),
            "data_status_zh": _status_zh(self.data_status),
            "trade_date": self.trade_date,
            "collected_at": self.collected_at,
            "data_cutoff": self.data_cutoff,
            "snapshot_id": self.snapshot_id,
            "coverage_ratio": self.coverage_ratio,
            "action": action_value,
            "action_zh": self.action_zh,
            "action_strength": self.action_strength,
            "allow_new_position": self.allow_new_position,
            "allow_add_position": self.allow_add_position,
            "recommend_reduce": self.recommend_reduce,
            "recommend_exit": self.recommend_exit,
            "veto_triggered": self.veto_triggered,
            "formal_score": self.formal_score,
            "enhanced_score": self.enhanced_score,
            "confidence": self.confidence,
            "consistency_status": self.consistency_status,
            "inconsistency_reason": self.inconsistency_reason,
            "current_position_ratio": self.current_position_ratio,
            "target_position_ratio": self.target_position_ratio,
            "position_change_ratio": self.position_change_ratio,
            "recommended_batches": self.recommended_batches,
            "price_zones": {
                "entry_zone": self.price_zones.entry_zone,
                "preferred_zone": self.price_zones.preferred_zone,
                "add_zone": self.price_zones.add_zone,
                "reduce_zone": self.price_zones.reduce_zone,
                "take_profit_zone": self.price_zones.take_profit_zone,
                "stop_loss_price": self.price_zones.stop_loss_price,
                "stop_loss_condition": self.price_zones.stop_loss_condition,
                "invalidation_condition": self.price_zones.invalidation_condition,
                "expected_holding_period": self.price_zones.expected_holding_period,
            },
            "supporting_reasons": self.supporting_reasons,
            "major_risks": self.major_risks,
            "missing_data": self.missing_data,
            "next_action": self.next_action,
            "execution_status": self.execution_status,
            "execution_status_reason": self.execution_status_reason,
            "frozen_entry_zone": self.frozen_entry_zone,
            "frozen_preferred_zone": self.frozen_preferred_zone,
            "frozen_stop_loss_price": self.frozen_stop_loss_price,
            "zone_version": self.zone_version,
        }


def _status_zh(s: DataStatus) -> str:
    from trading.decision_support.data_status import data_status_zh
    return data_status_zh(s)
