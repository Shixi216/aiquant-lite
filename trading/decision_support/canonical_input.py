from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from data_hub.schemas.unified import FactorType
from trading.decision_support.formal_score_resolver import (
    CROSS_SECTION_SOURCE,
    FormalScoreResolver,
)
from trading.decision_support.portfolio_context import PortfolioContext
from trading.decision_support.action_resolver import VetoResult
from trading.decision_support.data_status import DataStatus
from trading.decision_support.decision_engine import DecisionInput
from trading.decision_support.manual_position_manager import Holding
from trading.decision_support.price_zones import Bar
from trading.decision_support.task_context import TaskContext
from trading.decision_support.user_context import UserContext
from trading.research.capital_flow.models import MarketBar
from trading.research.orchestration.schemas import (
    DecisionShadowRequest,
    FactorAvailabilityStatus,
    FactorBundle,
    FactorView,
    FormalResult,
)


class PositionReader(Protocol):
    def get_holding(self, local_user_id: str, symbol: str) -> Holding | None: ...


@dataclass(frozen=True)
class CanonicalVetoContext:
    result: VetoResult | None
    source: str
    reason: str
    input_complete: bool


@dataclass(frozen=True)
class CanonicalDecisionBuildResult:
    decision_input: DecisionInput
    score_source: str
    score_source_mismatch: bool
    score_source_reason: str
    veto_source: str
    veto_reason: str
    veto_input_complete: bool
    position_ratio_source: str


def _score(factor: FactorView | None, *, missing_value: float = 0.0) -> float:
    return missing_value if factor is None else float(factor.score)


def _confidence(factor: FactorView | None, *, missing_value: float) -> float:
    return missing_value if factor is None else float(factor.confidence)


def _data_status(bundle: FactorBundle) -> DataStatus:
    core = (bundle.technical, bundle.fundamental)
    if any(factor is None for factor in core):
        return DataStatus.FAILED
    failed = {
        FactorAvailabilityStatus.CONFLICT,
        FactorAvailabilityStatus.MODE_RESTRICTED,
        FactorAvailabilityStatus.FUTURE_DATA_REJECTED,
    }
    if any(factor.availability_status in failed for factor in core if factor is not None):
        return DataStatus.FAILED
    if any(
        factor.availability_status == FactorAvailabilityStatus.STALE
        for factor in core
        if factor is not None
    ):
        return DataStatus.STALE
    # margin（两融）数据过期同样禁止建仓：四维 composite 的 margin 维度已失真
    if (
        bundle.margin is not None
        and bundle.margin.availability_status == FactorAvailabilityStatus.STALE
    ):
        return DataStatus.STALE
    degraded = {
        FactorAvailabilityStatus.PARTIAL,
        FactorAvailabilityStatus.UNVERIFIED,
    }
    if bundle.missing_factor_types or any(
        factor.availability_status in degraded for factor in bundle.factor_map().values()
    ):
        return DataStatus.DEGRADED
    return DataStatus.FRESH


def _point_in_time_ok(bundle: FactorBundle) -> bool:
    # PIT 四维（与四维 composite 的 tech/fund/sent/margin 对应）：
    # tech/fund 缺失即失败；sent/margin 存在时才检查（缺失不阻断）。
    required = (bundle.technical, bundle.fundamental)
    if any(factor is None for factor in required):
        return False
    optional = (bundle.sentiment, bundle.margin)
    return all(
        factor.data_cutoff <= bundle.data_cutoff
        and factor.availability_status
        != FactorAvailabilityStatus.FUTURE_DATA_REJECTED
        for factor in (*required, *optional)
        if factor is not None
    )


def _zone_bars(market_bars: list[MarketBar]) -> list[Bar]:
    valid = [
        item
        for item in sorted(market_bars, key=lambda value: value.event_time)
        if None not in (item.open, item.high, item.low, item.close, item.volume)
    ]
    return [
        Bar(
            trade_date=item.event_time.astimezone().date(),
            open=float(item.open),
            high=float(item.high),
            low=float(item.low),
            close=float(item.close),
            volume=float(item.volume),
        )
        for item in valid
    ]


class CanonicalDecisionInputBuilder:
    def __init__(self, position_reader: PositionReader) -> None:
        self.position_reader = position_reader
        self.formal_score_resolver = FormalScoreResolver()

    def build(
        self,
        *,
        request_id: str,
        user_context: UserContext,
        request: DecisionShadowRequest,
        bundle: FactorBundle,
        market_bars: list[MarketBar],
        legacy_result: FormalResult,
        portfolio_context: PortfolioContext,
        veto_context: CanonicalVetoContext,
    ) -> CanonicalDecisionBuildResult:
        if portfolio_context.local_user_id != user_context.local_user_id:
            raise ValueError("portfolio identity must match user context")
        technical_score = _score(bundle.technical)
        fundamental_score = _score(bundle.fundamental)
        resolved = self.formal_score_resolver.resolve(
            request.symbol,
            technical_score,
            fundamental_score,
            data_cutoff=bundle.data_cutoff,
        )
        if resolved.source == CROSS_SECTION_SOURCE:
            formal_score = resolved.score
            score_source = resolved.source
        else:
            # G 守卫（P0）：legacy 60/40 分数无区分能力（封顶 0.8、技术分占位），
            # 回退时强制 WAIT（0.0 落在 wait_min=-0.20 与 buy_min=0.22 之间），禁止新增。
            formal_score = 0.0
            score_source = resolved.source
        confidence = (
            _confidence(bundle.technical, missing_value=0.0) * 0.60
            + _confidence(bundle.fundamental, missing_value=0.1) * 0.40
        )
        mismatch_fields: list[str] = []
        if abs(request.technical_score - technical_score) > 1e-12:
            mismatch_fields.append("technical_score")
        if abs(request.fundamental_score - fundamental_score) > 1e-12:
            mismatch_fields.append("fundamental_score")
        if abs(legacy_result.score - formal_score) > 1e-12:
            mismatch_fields.append("formal_score")

        bars = _zone_bars(market_bars)
        current_price = bars[-1].close if bars else 0.0
        holding = portfolio_context.holding_for(request.symbol)
        if holding is None:
            holding = self.position_reader.get_holding(
                user_context.local_user_id, request.symbol
            )
        position_ratio_source = "NO_POSITION"
        if holding is not None and holding.quantity > 0:
            ratio = 0.0
            if current_price > 0 and portfolio_context.total_assets > 0:
                ratio = holding.quantity * current_price / portfolio_context.total_assets
            position_ratio_source = (
                "RESEARCH_ASSUMPTION_MARK_TO_MARKET"
                if portfolio_context.is_research_assumption
                else "ACCOUNT_CONTEXT_MARK_TO_MARKET"
            )
            holding = Holding(
                local_user_id=user_context.local_user_id,
                symbol=holding.symbol,
                stock_name=holding.stock_name,
                quantity=holding.quantity,
                average_cost=holding.average_cost,
                current_price=current_price,
                position_ratio=max(0.0, ratio),
                total_assets=portfolio_context.total_assets,
            )

        factor_map = bundle.factor_map()
        missing_data = [item.value for item in bundle.missing_factor_types]
        major_risks = list(
            dict.fromkeys(
                flag
                for factor in factor_map.values()
                for flag in factor.risk_flags
            )
        )
        decision_input = DecisionInput(
            task=TaskContext(
                local_user_id=user_context.local_user_id,
                external_user_id=user_context.external_user_id,
                channel=user_context.channel,
                task_id=request_id,
                mode="DECISION",
                symbol=request.symbol,
                snapshot_id=f"canonical-{bundle.input_snapshot_hash[:24]}",
                trade_date=(
                    str(bars[-1].trade_date) if bars else bundle.data_cutoff.date().isoformat()
                ),
                snapshot_time=bundle.data_cutoff,
                collected_at=bundle.data_cutoff,
                data_cutoff=bundle.data_cutoff,
            ),
            formal_score=formal_score,
            technical_score=technical_score,
            fundamental_score=fundamental_score,
            point_in_time_ok=_point_in_time_ok(bundle),
            account_context=portfolio_context,
            sentiment_score=_score(factor_map.get(FactorType.SENTIMENT)),
            policy_score=_score(factor_map.get(FactorType.POLICY_NEWS)),
            capital_score=_score(factor_map.get(FactorType.CAPITAL_FLOW)),
            confidence=max(0.0, min(1.0, confidence)),
            data_status=_data_status(bundle),
            veto=veto_context.result,
            current_price=current_price,
            bars=bars,
            coverage_ratio=bundle.factor_coverage.coverage_ratio,
            missing_data=missing_data,
            major_risks=major_risks,
            holding=holding,
            holding_context_provided=True,
            position_ratio_source=position_ratio_source,
        )
        return CanonicalDecisionBuildResult(
            decision_input=decision_input,
            score_source=score_source,
            score_source_mismatch=bool(mismatch_fields),
            score_source_reason=(
                f"{score_source}:"
                + ("SCORE_SOURCE_MISMATCH:" + ",".join(mismatch_fields)
                   if mismatch_fields
                   else ("LEGACY_60_40_FALLBACK_FORCE_WAIT"
                         if score_source == "legacy_60_40_fallback"
                         else "PIT_FACTOR_BUNDLE_MATCH"))
            ),
            veto_source=veto_context.source,
            veto_reason=veto_context.reason,
            veto_input_complete=veto_context.input_complete,
            position_ratio_source=position_ratio_source,
        )


__all__ = [
    "CanonicalDecisionBuildResult",
    "CanonicalDecisionInputBuilder",
    "CanonicalVetoContext",
]
