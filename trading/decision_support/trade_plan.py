"""交易计划 TradePlan（阶段2.1）

每个明确建议必须能够生成 TradePlan，至少包含 30+ 字段。
状态：DRAFT / PENDING_CONFIRMATION / CONFIRMED / ACTIVE / PARTIALLY_EXECUTED / COMPLETED / CANCELLED / EXPIRED

必须注明：价格区间是交易计划，不是自动委托。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import StrEnum
from typing import Optional

TZ = timezone(timedelta(hours=8))


class TradePlanStatus(StrEnum):
    DRAFT = "DRAFT"                          # 草稿
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"  # 待确认
    CONFIRMED = "CONFIRMED"                  # 已确认
    ACTIVE = "ACTIVE"                        # 生效中
    PARTIALLY_EXECUTED = "PARTIALLY_EXECUTED"  # 部分执行
    COMPLETED = "COMPLETED"                  # 已完成
    CANCELLED = "CANCELLED"                  # 已取消
    EXPIRED = "EXPIRED"                      # 已过期


TRADE_PLAN_STATUS_ZH = {
    TradePlanStatus.DRAFT: "草稿",
    TradePlanStatus.PENDING_CONFIRMATION: "待确认",
    TradePlanStatus.CONFIRMED: "已确认",
    TradePlanStatus.ACTIVE: "生效中",
    TradePlanStatus.PARTIALLY_EXECUTED: "部分执行",
    TradePlanStatus.COMPLETED: "已完成",
    TradePlanStatus.CANCELLED: "已取消",
    TradePlanStatus.EXPIRED: "已过期",
}


@dataclass
class TradePlan:
    # 标识
    plan_id: str
    local_user_id: str
    symbol: str
    action: str                       # STRONG_BUY/BUY/SMALL_BUY/ADD/HOLD/REDUCE/...
    stock_name: str = ""
    current_position: float = 0.0
    recommended_position: float = 0.0
    position_change: float = 0.0
    current_price: float = 0.0

    # 价格区间（交易计划，非自动委托）
    entry_zone: list = field(default_factory=list)         # 建仓区间 [low, high]
    add_zone: list = field(default_factory=list)           # 加仓区间
    reduce_zone: list = field(default_factory=list)        # 减仓区间
    stop_loss_price: Optional[float] = None
    stop_loss_condition: str = ""
    take_profit_zone: list = field(default_factory=list)   # 止盈区间
    invalidation_condition: str = ""                       # 决策失效条件
    expected_holding_period: str = ""
    risk_reward_ratio: float = 0.0

    # 评分与依据
    formal_score: float = 0.0
    enhanced_score: Optional[float] = None
    confidence: float = 0.0
    data_status: str = "FRESH"
    snapshot_id: str = ""
    data_cutoff: str = ""
    strategy_version: str = "formal-60-40-v1"
    veto_status: str = "NO_VETO"
    supporting_evidence: list = field(default_factory=list)
    major_risks: list = field(default_factory=list)

    # 生命周期
    status: TradePlanStatus = TradePlanStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=TZ))
    expires_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "local_user_id": self.local_user_id,
            "symbol": self.symbol,
            "stock_name": self.stock_name,
            "action": self.action,
            "current_position": self.current_position,
            "recommended_position": self.recommended_position,
            "position_change": self.position_change,
            "current_price": self.current_price,
            "entry_zone": self.entry_zone,
            "add_zone": self.add_zone,
            "reduce_zone": self.reduce_zone,
            "stop_loss_price": self.stop_loss_price,
            "stop_loss_condition": self.stop_loss_condition,
            "take_profit_zone": self.take_profit_zone,
            "invalidation_condition": self.invalidation_condition,
            "expected_holding_period": self.expected_holding_period,
            "risk_reward_ratio": self.risk_reward_ratio,
            "formal_score": self.formal_score,
            "enhanced_score": self.enhanced_score,
            "confidence": self.confidence,
            "data_status": self.data_status,
            "snapshot_id": self.snapshot_id,
            "data_cutoff": self.data_cutoff,
            "strategy_version": self.strategy_version,
            "veto_status": self.veto_status,
            "supporting_evidence": self.supporting_evidence,
            "major_risks": self.major_risks,
            "status": self.status.value,
            "status_zh": TRADE_PLAN_STATUS_ZH.get(self.status, self.status.value),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
