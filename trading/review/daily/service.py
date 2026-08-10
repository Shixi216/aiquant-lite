from __future__ import annotations

from datetime import date

from trading.decision_support.decision_packets import DecisionRepository
from manual_tracking.risk_reviews import (
    ManualPositionRiskReviewRepository,
)
from manual_tracking.trades import ManualTradeRepository
from trading.simulation.persistence import TradingAuditStore
from trading.review.attribution import (
    DecisionReadRepository,
    ManualPositionRiskReviewReadRepository,
    ManualTradeReadRepository,
    SimulationReadRepository,
)
from trading.review.daily.renderer import render_daily_review_markdown
from trading.schemas import (
    Action,
    DailyReview,
    DecisionPacket,
    ManualTrade,
)


class ReviewService:
    """Build one read-only review across decisions, simulations and manual facts."""

    def __init__(
        self,
        *,
        decisions: DecisionReadRepository,
        simulations: SimulationReadRepository,
        manual_trades: ManualTradeReadRepository,
        risk_reviews: ManualPositionRiskReviewReadRepository,
    ) -> None:
        self._decisions = decisions
        self._simulations = simulations
        self._manual_trades = manual_trades
        self._risk_reviews = risk_reviews

    def _load_packets(
        self,
        trades: list[ManualTrade],
        evidence_gaps: list[str],
    ) -> list[DecisionPacket]:
        packets: list[DecisionPacket] = []
        for decision_id in dict.fromkeys(
            trade.decision_id for trade in trades if trade.decision_id
        ):
            try:
                packets.append(self._decisions.get_latest(str(decision_id)))
            except Exception as exc:
                evidence_gaps.append(
                    f"DecisionPacket {decision_id} is not readable: "
                    f"{type(exc).__name__}: {str(exc)[:240]}"
                )
        return packets

    @staticmethod
    def _trade_deviations(
        trades: list[ManualTrade],
        packets: list[DecisionPacket],
    ) -> list[str]:
        packets_by_id = {packet.decision_id: packet for packet in packets}
        deviations: list[str] = []
        for trade in trades:
            if not trade.decision_id:
                deviations.append(
                    f"Manual trade {trade.trade_id} has no linked DecisionPacket."
                )
                continue
            packet = packets_by_id.get(trade.decision_id)
            if packet is None:
                continue
            expected = packet.decision.final_action
            aligned = (
                trade.side.value == "BUY" and expected == Action.BUY
            ) or (
                trade.side.value == "SELL" and expected == Action.SELL
            )
            if not aligned:
                deviations.append(
                    f"Manual trade {trade.trade_id} ({trade.side.value}) differs "
                    f"from DecisionPacket {packet.decision_id} v"
                    f"{packet.decision_version} ({expected.value})."
                )
            if trade.traded_at < packet.data_cutoff_time:
                deviations.append(
                    f"Manual trade {trade.trade_id} predates the linked "
                    "DecisionPacket data cutoff."
                )
        return deviations

    def daily_review(self, review_date: date) -> DailyReview:
        simulation_review = self._simulations.daily_review(review_date)
        all_trades = self._manual_trades.list_trades()
        daily_trades = [
            trade for trade in all_trades if trade.traded_at.date() == review_date
        ]
        positions = self._manual_trades.list_positions()
        risk_reviews = self._risk_reviews.list_for_date(review_date)

        relevant_position_ids = {review.position_id for review in risk_reviews}
        relevant_trade_ids = {
            trade_id
            for position in positions
            if position.position_id in relevant_position_ids
            for trade_id in position.source_trade_ids
        }
        packet_source_trades = [
            trade
            for trade in all_trades
            if trade in daily_trades or trade.trade_id in relevant_trade_ids
        ]
        evidence_gaps: list[str] = []
        packets = self._load_packets(packet_source_trades, evidence_gaps)
        deviations = self._trade_deviations(daily_trades, packets)

        simulation_by_symbol = {}
        for result in simulation_review.orders:
            simulation_by_symbol.setdefault(result.symbol, []).append(result)
        for trade in daily_trades:
            comparable = simulation_by_symbol.get(trade.symbol, [])
            if comparable and all(
                result.side.value.upper() != trade.side.value
                for result in comparable
            ):
                deviations.append(
                    f"Manual trade {trade.trade_id} direction differs from all "
                    f"same-day simulation results for {trade.symbol}."
                )

        packet_by_id = {packet.decision_id: packet for packet in packets}
        model_errors: list[str] = []
        data_quality_issues: list[str] = []
        for packet in packets:
            evidence_gaps.extend(
                f"DecisionPacket {packet.decision_id} v{packet.decision_version}: "
                f"{item}"
                for item in packet.missing_information
            )
            model_errors.extend(
                f"DecisionPacket {packet.decision_id} v{packet.decision_version} "
                f"agent disagreement: {item}"
                for item in packet.agent_disagreements
            )
            if packet.evidence_quality < 0.5:
                data_quality_issues.append(
                    f"DecisionPacket {packet.decision_id} v"
                    f"{packet.decision_version} evidence quality is "
                    f"{packet.evidence_quality:.0%}."
                )

        positions_by_id = {position.position_id: position for position in positions}
        for review in risk_reviews:
            evidence_gaps.extend(
                f"Risk review {review.review_id}: {item}"
                for item in review.missing_information
            )
            if review.original_thesis_status.value == "INVALIDATED":
                model_errors.append(
                    f"Risk review {review.review_id} invalidated the original "
                    f"thesis for position {review.position_id}."
                )
            if not review.evidence_record_ids:
                data_quality_issues.append(
                    f"Risk review {review.review_id} has no traceable evidence."
                )
            position = positions_by_id.get(review.position_id)
            if position is None:
                data_quality_issues.append(
                    f"Risk review {review.review_id} references a missing "
                    "manual-position projection."
                )
            elif (
                position.quantity != 0
                and review.recommended_action.value
                in {"CONSIDER_REDUCING", "CONSIDER_EXITING"}
            ):
                linked_packets = [
                    packet_by_id[trade.decision_id]
                    for trade in all_trades
                    if trade.trade_id in position.source_trade_ids
                    and trade.decision_id in packet_by_id
                ]
                packet_label = (
                    "without a linked readable DecisionPacket"
                    if not linked_packets
                    else f"against DecisionPacket {linked_packets[-1].decision_id}"
                )
                deviations.append(
                    f"Position {position.position_id} remains open after risk "
                    f"review {review.review_id} recommended "
                    f"{review.recommended_action.value} {packet_label}; human "
                    "assessment is pending."
                )

        evidence_gaps = list(dict.fromkeys(evidence_gaps))
        deviations = list(dict.fromkeys(deviations))
        model_errors = list(dict.fromkeys(model_errors))
        data_quality_issues = list(dict.fromkeys(data_quality_issues))
        summary = [
            *simulation_review.summary,
            f"{len(packets)} immutable DecisionPackets were compared.",
            f"{len(daily_trades)} user-reported manual trades occurred.",
            f"{len(positions)} current manual-position projections were read.",
            f"{len(risk_reviews)} manual-position risk reviews were recorded.",
            f"{len(deviations)} potential plan deviations require review.",
            f"{len(evidence_gaps)} evidence gaps and "
            f"{len(data_quality_issues)} data-quality issues were found.",
        ]
        return simulation_review.model_copy(
            update={
                "summary": summary,
                "decision_packets": packets,
                "manual_trades": daily_trades,
                "manual_positions": positions,
                "manual_position_risk_reviews": risk_reviews,
                "decision_deviations": deviations,
                "model_errors": model_errors,
                "evidence_gaps": evidence_gaps,
                "data_quality_issues": data_quality_issues,
            }
        )

    def daily_review_markdown(self, review_date: date) -> str:
        return render_daily_review_markdown(self.daily_review(review_date))


def build_review_service(
    *,
    decisions: DecisionRepository | None = None,
    simulations: TradingAuditStore | None = None,
    manual_trades: ManualTradeRepository | None = None,
    risk_reviews: ManualPositionRiskReviewRepository | None = None,
) -> ReviewService:
    """Wire concrete stores behind capability-limited read-only views."""

    return ReviewService(
        decisions=DecisionReadRepository(decisions or DecisionRepository()),
        simulations=SimulationReadRepository(simulations or TradingAuditStore()),
        manual_trades=ManualTradeReadRepository(
            manual_trades or ManualTradeRepository()
        ),
        risk_reviews=ManualPositionRiskReviewReadRepository(
            risk_reviews or ManualPositionRiskReviewRepository()
        ),
    )
