"""决策编排引擎 DecisionEngine（阶段2.6）

完整决策链：
TaskContext → 数据状态 → VETO → 动作解析 → 仓位计算 → 价格区间 → TradePlan → DecisionResponse

决策优先级（阶段1.6）：
1. 实盘权限禁用检查（永远拒绝）
2. 用户权限检查
3. 数据状态检查（STALE禁止建仓 / FAILED停止）
4. point-in-time 检查
5. 硬VETO检查（覆盖一切正向评分）
6. 用户当前持仓检查
7. 正式60/40评分
8. 增强层调整
9. 生成最终动作
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Optional

from trading.decision_support.portfolio_context import PortfolioContext
from trading.decision_support.action import Action, action_zh
from trading.decision_support.action_resolver import (
    DecisionActionResolver, ResolverInput, ResolverOutput, VetoResult,
)
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_response import DecisionResponse, PriceZone
from trading.decision_support.permission_service import PermissionService
from trading.decision_support.position_sizer import PositionSizer, SizerInput, SizerOutput
from trading.decision_support.price_zones import PriceZoneCalculator
from trading.decision_support.task_context import TaskContext
from trading.decision_support.thresholds import ActionThresholdsV2
from trading.decision_support.trade_plan import TradePlan, TradePlanStatus
from trading.decision_support.trade_plan_repository import TradePlanRepository
from trading.decision_support.manual_position_manager import Holding, ManualPositionManager
from trading.decision_support.sentiment_v3_advisor import SentimentAdvisoryResult

TZ = timezone(timedelta(hours=8))


@dataclass
class DecisionInput:
    task: TaskContext
    formal_score: float
    technical_score: float
    fundamental_score: float
    point_in_time_ok: bool
    account_context: PortfolioContext
    sentiment_score: float = 0.0
    policy_score: float = 0.0
    capital_score: float = 0.0
    confidence: float = 0.5
    data_status: DataStatus = DataStatus.FRESH
    veto: Optional[VetoResult] = None
    current_price: float = 0.0
    bars: list = None                    # K线（价格区间计算用）
    coverage_ratio: float = 0.0
    missing_data: list = None
    major_risks: list = None
    enhanced_score: Optional[float] = None
    consistency_status: str = "NA"
    inconsistency_reason: str = ""
    holding: Optional[Holding] = None
    holding_context_provided: bool = False
    sentiment_v3_advisory: SentimentAdvisoryResult | None = None
    position_ratio_source: str = "UNSPECIFIED"


class DecisionEngine:
    """完整决策链编排"""

    def __init__(
        self,
        permission_service: PermissionService | None = None,
        resolver: DecisionActionResolver | None = None,
        sizer: PositionSizer | None = None,
        zones: PriceZoneCalculator | None = None,
        plan_repo: TradePlanRepository | None = None,
        manual_positions: ManualPositionManager | None = None,
        thresholds: ActionThresholdsV2 | None = None,
    ):
        self.permissions = permission_service or PermissionService()
        thresholds = thresholds or ActionThresholdsV2.from_table()
        self.resolver = resolver or DecisionActionResolver(thresholds)
        self.sizer = sizer or PositionSizer(thresholds)
        self.zones = zones or PriceZoneCalculator()
        self.plan_repo = plan_repo or TradePlanRepository()
        self.manual_positions = manual_positions or ManualPositionManager()

    def decide(self, inp: DecisionInput) -> DecisionResponse:
        ctx = inp.task

        # 1. 实盘权限禁用检查（LEVEL4 永远拒绝，由 PermissionService 保证）
        assert not self.permissions.can_live_trade(ctx.local_user_id)

        # 2. 用户权限检查（LEVEL2 分析：默认允许——分析不写数据，无风险；
        #    LEVEL3 写入权限由写入服务单独检查）

        # 3-5. 数据状态/VETO/持仓 → 动作
        holding = inp.holding
        position_ratio_source = inp.position_ratio_source
        if not inp.holding_context_provided:
            holding = (
                self.manual_positions.get_holding(ctx.local_user_id, ctx.symbol)
                if ctx.symbol
                else None
            )
            position_ratio_source = (
                "MANUAL_LEDGER_QUANTITY_ONLY"
                if holding is not None and holding.quantity > 0
                else "NO_POSITION"
            )
        has_position = holding is not None and holding.quantity > 0

        resolver_input = ResolverInput(
            formal_score=inp.formal_score,
            technical_score=inp.technical_score,
            fundamental_score=inp.fundamental_score,
            sentiment_score=inp.sentiment_score,
            policy_score=inp.policy_score,
            capital_score=inp.capital_score,
            confidence=inp.confidence,
            data_status=inp.data_status,
            veto=inp.veto,
            has_position=has_position,
            current_position_ratio=holding.position_ratio if holding else 0.0,
            average_cost=holding.average_cost if holding else 0.0,
            current_price=inp.current_price,
            unrealized_return=((inp.current_price - holding.average_cost) / holding.average_cost
                               if holding and holding.average_cost else 0),
            user_risk_level="MEDIUM",
            snapshot_id=ctx.snapshot_id,
            data_cutoff=str(ctx.data_cutoff) if ctx.data_cutoff else "",
            point_in_time_ok=inp.point_in_time_ok,
        )

        # Sentiment V3 Advisory 注入
        if inp.sentiment_v3_advisory is not None:
            resolver_input.sentiment_risk_reminder = (
                inp.sentiment_v3_advisory.risk_reminder
            )
            resolver_input.sentiment_v3_composite = (
                inp.sentiment_v3_advisory.composite_score
            )
            resolver_input.sentiment_v3_risk_details = (
                inp.sentiment_v3_advisory.risk_details
            )

        out: ResolverOutput = self.resolver.resolve(resolver_input)

        # 6-8. 仓位计算
        sizer_input = SizerInput(
            total_assets=inp.account_context.total_assets,
            available_cash=inp.account_context.available_cash,
            current_position_ratio=holding.position_ratio if holding else 0.0,
            formal_score=inp.formal_score,
            confidence=out.confidence,
            data_status=inp.data_status,
            veto_triggered=inp.veto.veto_triggered if inp.veto else False,
            action=out.action,
        )
        sizing: SizerOutput = self.sizer.size(sizer_input)

        # 9. 价格区间
        zone_dict = self.zones.build_zones(
            inp.bars or [], inp.current_price, out.action,
            confidence=out.confidence,
            average_cost=holding.average_cost if holding else None,
        )

        # 组装响应
        resp = DecisionResponse(
            task_context=ctx,
            data_status=inp.data_status,
            snapshot_id=ctx.snapshot_id,
            data_cutoff=str(ctx.data_cutoff) if ctx.data_cutoff else "",
            coverage_ratio=inp.coverage_ratio,
            action=out.action,
            action_zh=action_zh(out.action),
            action_strength=out.action_strength,
            allow_new_position=out.allow_new_position,
            allow_add_position=out.allow_add_position,
            recommend_reduce=out.recommend_reduce,
            recommend_exit=out.recommend_exit,
            veto_triggered=out.veto_override,
            formal_score=inp.formal_score,
            enhanced_score=inp.enhanced_score,
            confidence=out.confidence,
            sentiment_v3_risk_reminder=resolver_input.sentiment_risk_reminder,
            consistency_status=inp.consistency_status,
            inconsistency_reason=inp.inconsistency_reason,
            current_position_ratio=sizing.current_position_ratio,
            target_position_ratio=sizing.target_position_ratio,
            position_change_ratio=sizing.position_change_ratio,
            recommended_batches=sizing.recommended_batches,
            account_context_source=inp.account_context.source,
            account_context_is_research_assumption=(
                inp.account_context.is_research_assumption
            ),
            position_ratio_source=position_ratio_source,
            price_zones=PriceZone(
                entry_zone=zone_dict.get("entry_zone", []),
                preferred_zone=zone_dict.get("preferred_zone", []),
                add_zone=zone_dict.get("add_zone", []),
                reduce_zone=zone_dict.get("reduce_zone", []),
                take_profit_zone=zone_dict.get("take_profit_zone", []),
                stop_loss_price=zone_dict.get("stop_loss_price"),
                stop_loss_condition=zone_dict.get("stop_loss_condition", ""),
                invalidation_condition=zone_dict.get("invalidation_condition", ""),
                expected_holding_period=zone_dict.get("expected_holding_period", ""),
            ),
            major_risks=inp.major_risks or [],
            missing_data=inp.missing_data or [],
            supporting_reasons=[
                f"正式评分 {inp.formal_score:+.2f}",
                f"动作 {action_zh(out.action)}",
                f"目标仓位 {sizing.target_position_ratio:.0%}",
            ],
        )
        resp.next_action = self._next_action(out, sizing)
        # 执行状态：基于冻结区间计算
        from trading.decision_support.execution_status import compute_execution_status
        veto_triggered = inp.veto.veto_triggered if inp.veto else False
        action_value = out.action.value if hasattr(out.action, "value") else str(out.action)
        status, reason = compute_execution_status(
            current_price=inp.current_price,
            action=action_value,
            veto_triggered=veto_triggered,
            frozen_entry_zone=zone_dict.get("entry_zone", []),
            frozen_preferred_zone=zone_dict.get("preferred_zone", []),
            frozen_stop_loss_price=zone_dict.get("stop_loss_price", 0),
        )
        resp.execution_status = status
        resp.execution_status_reason = reason
        # 冻结区间（供 DecisionPacket 使用）
        resp.frozen_entry_zone = zone_dict.get("entry_zone", [])
        resp.frozen_preferred_zone = zone_dict.get("preferred_zone", [])
        resp.frozen_stop_loss_price = zone_dict.get("stop_loss_price", 0)
        resp.zone_version = "V1"
        return resp

    def _compute_execution_status(
        self, action: Action, current_price: float,
        zone_dict: dict, veto: Optional[VetoResult]
    ) -> str:
        """计算执行状态（独立于正式动作）

        规则：
        - VETO触发/回避 → 已失效
        - 建仓类 + 当前价≤优选区间上沿 → 立即执行首批
        - 建仓类 + 当前价>优选区间上沿 且 ≤允许建仓区间上沿 → 等待回调
        - 建仓类 + 当前价>允许建仓区间上沿 → 禁止追高
        - 建仓类 + 当前价≤支撑/失效价 → 信号失效
        - 加仓 → 立即执行
        - 等待/持有 → 等待确认
        - 减仓/止盈/止损/清仓 → 立即执行
        """
        # === 优先级1：VETO触发 → 已失效（覆盖一切）===
        if veto and veto.veto_triggered:
            return "已失效"
        # === 优先级2：止损价检查（优先于价格区间）===
        stop = zone_dict.get("stop_loss_price")
        if stop and current_price <= stop:
            return "信号失效"
        # === 优先级3：回避 → 已失效 ===
        if action == Action.AVOID:
            return "已失效"
        # === 建仓类动作 ===
        if action in (Action.STRONG_BUY, Action.BUY, Action.SMALL_BUY):
            entry = zone_dict.get("entry_zone", [])
            preferred = zone_dict.get("preferred_zone", [])
            if preferred and len(preferred) == 2:
                pref_low = preferred[0]
                pref_high = preferred[1]
                if pref_low <= current_price <= pref_high:
                    return "立即执行"
                elif current_price < pref_low:
                    return "等待止跌确认"
                elif entry and len(entry) == 2 and current_price <= entry[1]:
                    return "等待回调"
                else:
                    return "禁止追高"
            elif entry and len(entry) == 2:
                if current_price <= entry[1]:
                    return "立即执行"
                else:
                    return "禁止追高"
            return "等待回调"
        # 加仓
        if action == Action.ADD:
            return "立即执行"
        # 等待/持有
        if action in (Action.WAIT, Action.HOLD):
            return "等待确认"
        # 减仓/止盈/止损/清仓
        if action in (Action.REDUCE, Action.TAKE_PROFIT, Action.STOP_LOSS, Action.EXIT):
            return "立即执行"
        return "等待确认"

    # ------------------------------------------------------------------
    # 生成 TradePlan（草稿，需用户确认）
    # ------------------------------------------------------------------
    def create_trade_plan(self, resp: DecisionResponse, stock_name: str = "") -> TradePlan:
        ctx = resp.task_context
        plan = TradePlan(
            plan_id=f"plan_{uuid.uuid4().hex[:16]}",
            local_user_id=ctx.local_user_id,
            symbol=ctx.symbol,
            stock_name=stock_name,
            action=resp.action.value,
            current_position=resp.current_position_ratio,
            recommended_position=resp.target_position_ratio,
            position_change=resp.position_change_ratio,
            current_price=resp.price_zones.entry_zone[0] if resp.price_zones.entry_zone else 0,
            entry_zone=resp.price_zones.entry_zone,
            add_zone=resp.price_zones.add_zone,
            reduce_zone=resp.price_zones.reduce_zone,
            stop_loss_price=resp.price_zones.stop_loss_price,
            stop_loss_condition=resp.price_zones.stop_loss_condition,
            take_profit_zone=resp.price_zones.take_profit_zone,
            invalidation_condition=resp.price_zones.invalidation_condition,
            expected_holding_period=resp.price_zones.expected_holding_period,
            formal_score=resp.formal_score,
            enhanced_score=resp.enhanced_score,
            confidence=resp.confidence,
            data_status=resp.data_status.value,
            snapshot_id=resp.snapshot_id,
            data_cutoff=resp.data_cutoff,
            veto_status="VETO" if resp.veto_triggered else "NO_VETO",
            major_risks=resp.major_risks,
            status=TradePlanStatus.PENDING_CONFIRMATION,
        )
        self.plan_repo.create_plan(plan)
        return plan

    def _next_action(self, out: ResolverOutput, sizing: SizerOutput) -> str:
        if out.veto_override:
            return "核验风险公告，等待VETO解除"
        if out.action in (Action.STRONG_BUY, Action.BUY, Action.SMALL_BUY):
            return "创建交易计划并确认建仓"
        if out.action in (Action.REDUCE, Action.EXIT, Action.STOP_LOSS):
            return "创建减仓/清仓计划"
        if out.action == Action.HOLD:
            return "继续持有，更新人工持仓"
        return "等待价格进入区间或信号确认"
