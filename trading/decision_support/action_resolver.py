"""确定性动作解析器 DecisionActionResolver（阶段1.7）

输入：五维评分 + 置信度 + 数据状态 + VETO + 当前持仓
输出：明确动作（不依赖大模型自由生成）

决策优先级（阶段1.6）：
1. 实盘权限禁用检查（永远拒绝）
2. 用户权限检查
3. 数据状态检查（STALE禁止建仓 / FAILED停止）
4. point-in-time 检查
5. 硬VETO检查（覆盖一切正向评分）
6. 用户当前持仓检查
7. 正式60/40评分
8. 增强层对置信度/仓位/节奏调整
9. 生成最终动作
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from trading.decision_support.action import Action, action_zh
from trading.decision_support.data_status import DataStatus
from trading.decision_support.thresholds import ActionThresholds, THRESHOLD_VERSION


@dataclass
class VetoResult:
    veto_triggered: bool = False
    veto_type: str = ""
    veto_reason: str = ""
    evidence_source: str = ""
    evidence_time: str = ""
    confidence: float = 0.0
    action_override: str = ""


@dataclass
class ResolverInput:
    formal_score: float = 0.0
    technical_score: float = 0.0
    fundamental_score: float = 0.0
    sentiment_score: float = 0.0
    policy_score: float = 0.0
    capital_score: float = 0.0
    confidence: float = 0.5
    data_status: DataStatus = DataStatus.FRESH
    veto: Optional[VetoResult] = None
    has_position: bool = False          # 是否已持仓
    current_position_ratio: float = 0.0
    average_cost: float = 0.0
    current_price: float = 0.0
    unrealized_return: float = 0.0
    user_risk_level: str = "MEDIUM"
    snapshot_id: str = ""
    data_cutoff: str = ""
    point_in_time_ok: bool = True


@dataclass
class ResolverOutput:
    action: Action = Action.WAIT
    action_strength: str = "normal"     # strong / normal / weak
    action_reason_codes: list[str] = field(default_factory=list)
    allow_new_position: bool = False
    allow_add_position: bool = False
    recommend_reduce: bool = False
    recommend_exit: bool = False
    confidence: float = 0.0
    data_status: DataStatus = DataStatus.FRESH
    veto_override: bool = False
    threshold_version: str = THRESHOLD_VERSION


class DecisionActionResolver:
    """确定性动作解析器：阈值驱动，不依赖大模型"""

    def __init__(self, thresholds: ActionThresholds | None = None):
        self.t = thresholds or ActionThresholds()

    # ------------------------------------------------------------------
    # 决策优先级执行
    # ------------------------------------------------------------------
    def resolve(self, inp: ResolverInput) -> ResolverOutput:
        out = ResolverOutput(
            confidence=inp.confidence,
            data_status=inp.data_status,
        )

        # 1. 实盘权限禁用检查（永不启用——本解析器不产出实盘指令，占位保证）
        # 2. 用户权限检查（由 PermissionService 前置执行）

        # 3. 数据状态检查
        if inp.data_status == DataStatus.FAILED:
            out.action = Action.WAIT
            out.action_reason_codes.append("DATA_FAILED_STOP_DECISION")
            out.allow_new_position = False
            out.allow_add_position = False
            return out
        if inp.data_status == DataStatus.STALE:
            out.action = Action.WAIT
            out.action_reason_codes.append("DATA_STALE_NO_BUY")
            out.allow_new_position = False
            out.allow_add_position = False
            return out
        if inp.data_status == DataStatus.DEGRADED:
            out.action_reason_codes.append("DATA_DEGRADED_REDUCE_POSITION")

        # 4. point-in-time 检查
        if not inp.point_in_time_ok:
            out.action = Action.WAIT
            out.action_reason_codes.append("POINT_IN_TIME_FAILED")
            return out

        # 5. 硬VETO检查（覆盖一切正向评分）
        if inp.veto is not None and inp.veto.veto_triggered:
            out.veto_override = True
            out.action_reason_codes.append(f"VETO_{inp.veto.veto_type}")
            if inp.has_position:
                out.action = Action.EXIT
                out.recommend_exit = True
                out.action_reason_codes.append("VETO_EXIT")
            else:
                out.action = Action.AVOID
                out.action_reason_codes.append("VETO_AVOID")
            out.allow_new_position = False
            out.allow_add_position = False
            return out

        # 6. 用户当前持仓检查 + 7. 正式评分 → 动作
        if not inp.has_position:
            self._resolve_no_position(inp, out)
        else:
            self._resolve_with_position(inp, out)

        # 8. 增强层调整（情绪/政策/资金影响置信度和仓位，不改变动作方向）
        # 低置信度限制
        if inp.confidence < self.t.low_confidence:
            if out.action in (Action.STRONG_BUY, Action.BUY):
                out.action = Action.SMALL_BUY
                out.action_reason_codes.append("LOW_CONFIDENCE_DOWNGRADE")
            if out.action == Action.EXIT and not inp.veto:
                out.action = Action.REDUCE
                out.action_reason_codes.append("LOW_CONFIDENCE_NO_FULL_EXIT")

        # 9. 输出标志
        out.allow_new_position = out.action in (Action.STRONG_BUY, Action.BUY, Action.SMALL_BUY)
        out.allow_add_position = out.action == Action.ADD
        out.recommend_reduce = out.action in (Action.REDUCE, Action.EXIT, Action.STOP_LOSS)
        out.recommend_exit = out.action in (Action.EXIT, Action.STOP_LOSS)
        return out

    # ------------------------------------------------------------------
    # 无持仓动作（阶段1.7 阈值映射）
    # ------------------------------------------------------------------
    def _resolve_no_position(self, inp: ResolverInput, out: ResolverOutput) -> None:
        s = inp.formal_score
        if s >= self.t.strong_buy_min and inp.confidence >= self.t.strong_buy_conf:
            out.action = Action.STRONG_BUY
            out.action_strength = "strong"
            out.action_reason_codes.append("SCORE_GE_0.65_CONF_GE_0.70")
        elif s >= self.t.strong_buy_min:
            out.action = Action.BUY
            out.action_reason_codes.append("SCORE_GE_0.65_LOW_CONF")
        elif s >= self.t.buy_min and inp.confidence >= self.t.buy_conf:
            out.action = Action.BUY
            out.action_reason_codes.append("SCORE_0.40_TO_0.65_CONF_GE_0.60")
        elif s >= self.t.buy_min:
            out.action = Action.SMALL_BUY
            out.action_reason_codes.append("SCORE_0.40_TO_0.65_LOW_CONF")
        elif s >= self.t.wait_min:
            out.action = Action.WAIT
            out.action_reason_codes.append("SCORE_MINUS_0.20_TO_0.40_WAIT")
        else:
            out.action = Action.AVOID
            out.action_reason_codes.append("SCORE_LT_MINUS_0.20_AVOID")

    # ------------------------------------------------------------------
    # 已持仓动作
    # ------------------------------------------------------------------
    def _resolve_with_position(self, inp: ResolverInput, out: ResolverOutput) -> None:
        s = inp.formal_score
        if s >= self.t.hold_add_min:
            # 趋势与资金确认
            if inp.technical_score > 0 and inp.capital_score > 0:
                out.action = Action.ADD
                out.action_reason_codes.append("SCORE_GE_0.65_TREND_CAPITAL_OK")
            else:
                out.action = Action.HOLD
                out.action_reason_codes.append("SCORE_GE_0.65_NO_CONFIRM")
        elif s >= self.t.hold_min:
            out.action = Action.HOLD
            out.action_reason_codes.append("SCORE_0.20_TO_0.65_HOLD")
        elif s >= self.t.reduce_watch_max:
            out.action = Action.HOLD
            out.action_reason_codes.append("SCORE_MINUS_0.20_TO_0.20_HOLD_NO_ADD")
        elif s >= self.t.reduce_min:
            out.action = Action.REDUCE
            out.action_reason_codes.append("SCORE_MINUS_0.50_TO_MINUS_0.20_REDUCE")
        else:
            # 亏损且评分极低 → 止损；否则清仓
            if inp.unrealized_return < -0.15:
                out.action = Action.STOP_LOSS
                out.action_reason_codes.append("SCORE_LT_MINUS_0.50_LOSS_STOP_LOSS")
            else:
                out.action = Action.EXIT
                out.action_reason_codes.append("SCORE_LT_MINUS_0.50_EXIT")
