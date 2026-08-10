"""仓位计算器 PositionSizer（阶段1.8）

输入：总资产/现金/当前仓位/波动率/评分/置信度/数据状态/VETO/风险等级
输出：当前仓位/目标仓位/调整比例/分批次数/上限/原因码

默认仓位框架：
- 试错仓：2%~5%
- 初始仓：5%~10%
- 普通目标仓：10%~15%
- 高置信目标仓：15%~20%
- 单股最大仓位默认 20%
- 高风险股票最大仓位 5%

约束：
- DEGRADED 至少降低一个仓位等级
- STALE 和 FAILED 目标新增仓位必须为 0
- VETO 触发时不得新增仓位
- 不建议杠杆 / 融资满仓 / 无限补仓
- 不得因为亏损自动加仓摊薄成本
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trading.decision_support.action import Action
from trading.decision_support.data_status import DataStatus
from trading.decision_support.thresholds import ActionThresholds


@dataclass
class SizerInput:
    total_assets: float = 100_000.0
    available_cash: float = 100_000.0
    current_position_ratio: float = 0.0      # 当前单股仓位
    current_sector_ratio: float = 0.0        # 当前行业仓位
    current_portfolio_ratio: float = 0.0     # 当前总仓位
    volatility: float = 0.0                  # 波动率
    atr: float = 0.0
    formal_score: float = 0.0
    confidence: float = 0.5
    data_status: DataStatus = DataStatus.FRESH
    veto_triggered: bool = False
    user_risk_level: str = "MEDIUM"          # LOW / MEDIUM / HIGH
    max_single_position: float = 0.20
    max_sector_position: float = 0.35
    action: Action = Action.WAIT
    high_risk: bool = False                  # 高风险股票（VETO类型标记）


@dataclass
class SizerOutput:
    current_position_ratio: float = 0.0
    target_position_ratio: float = 0.0
    position_change_ratio: float = 0.0
    recommended_batches: int = 1
    max_allowed_position: float = 0.20
    sizing_reason_codes: list[str] = field(default_factory=list)
    allowed: bool = False


class PositionSizer:
    """确定性仓位计算器"""

    def __init__(self, thresholds: ActionThresholds | None = None):
        self.t = thresholds or ActionThresholds()

    def size(self, inp: SizerInput) -> SizerOutput:
        out = SizerOutput(
            current_position_ratio=inp.current_position_ratio,
            max_allowed_position=inp.max_single_position,
        )

        # 高风险股票上限
        if inp.high_risk or inp.user_risk_level == "HIGH":
            out.max_allowed_position = min(inp.max_single_position, self.t.risk_high_max_position)
            out.sizing_reason_codes.append("HIGH_RISK_CAP_5PCT")

        # STALE/FAILED：禁止新增
        if inp.data_status in (DataStatus.STALE, DataStatus.FAILED):
            out.target_position_ratio = inp.current_position_ratio
            out.position_change_ratio = 0.0
            out.recommended_batches = 0
            out.allowed = False
            out.sizing_reason_codes.append(f"{inp.data_status.value}_NO_NEW_POSITION")
            return out

        # VETO：不得新增
        if inp.veto_triggered:
            out.target_position_ratio = min(inp.current_position_ratio, out.max_allowed_position)
            out.position_change_ratio = 0.0
            out.allowed = False
            out.sizing_reason_codes.append("VETO_NO_NEW_POSITION")
            return out

        # 基础目标仓位（按动作）
        target = self._base_target(inp)

        # 风险等级调整
        if inp.user_risk_level == "LOW":
            target = min(target, 0.10)
            out.sizing_reason_codes.append("LOW_RISK_USER_CAP_10PCT")

        # 波动率调整（高波动降仓）
        if inp.volatility > 0.05:
            target *= 0.8
            out.sizing_reason_codes.append("HIGH_VOLATILITY_DISCOUNT")

        # DEGRADED：降低一个仓位等级
        if inp.data_status == DataStatus.DEGRADED:
            target *= 0.5
            out.sizing_reason_codes.append("DATA_DEGRADED_HALF")

        # 行业集中度约束
        remaining_sector = max(0.0, inp.max_sector_position - inp.current_sector_ratio)
        target = min(target, remaining_sector)
        out.sizing_reason_codes.append("SECTOR_CONCENTRATION_CAP")

        # 可用现金约束
        if inp.total_assets > 0:
            cash_ratio = inp.available_cash / inp.total_assets
            target = min(target, cash_ratio)
            out.sizing_reason_codes.append("CASH_CONSTRAINT")

        # 单股上限
        target = min(target, out.max_allowed_position)

        # 减仓动作：目标 ≤ 当前
        if inp.action in (Action.REDUCE, Action.STOP_LOSS, Action.EXIT):
            target = min(target, inp.current_position_ratio)
            out.sizing_reason_codes.append("REDUCE_ACTION_TARGET")

        out.target_position_ratio = round(target, 4)
        out.position_change_ratio = round(target - inp.current_position_ratio, 4)
        out.recommended_batches = self._batches(abs(out.position_change_ratio), target)
        out.allowed = out.position_change_ratio > 0.001
        return out

    def _base_target(self, inp: SizerInput) -> float:
        """基础目标仓位（按动作 + 评分 + 置信度）"""
        if inp.action in (Action.STRONG_BUY,):
            return 0.18 if inp.confidence >= 0.70 else 0.12
        if inp.action == Action.BUY:
            return 0.12 if inp.confidence >= 0.60 else 0.08
        if inp.action == Action.SMALL_BUY:
            return 0.03
        if inp.action == Action.ADD:
            return min(inp.current_position_ratio + 0.05, 0.20)
        if inp.action in (Action.REDUCE,):
            return max(inp.current_position_ratio - 0.05, 0.0)
        if inp.action in (Action.EXIT, Action.STOP_LOSS):
            return 0.0
        # WAIT/HOLD/AVOID/TAKE_PROFIT
        return inp.current_position_ratio

    def _batches(self, change: float, target: float) -> int:
        """分批次数：按调整幅度"""
        if change <= 0.001:
            return 0
        if change <= 0.05:
            return 1
        if change <= 0.10:
            return 2
        return 3
