from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

from router.schemas import RiskLevel, RiskReviewOutput, RouterInvokeRequest
from router.services.invocation import RouterInvocationService
from manual_tracking.risk_reviews.repository import (
    ManualPositionRiskReviewRepository,
)
from trading.research.events.position_data import (
    PersistedResearchRecord,
    PositionResearchReader,
)
from trading.review.attribution import (
    DecisionReadRepository,
    ManualTradeReadRepository,
)
from trading.schemas import (
    DecisionPacket,
    ManualPosition,
    ManualPositionRiskLevel,
    ManualPositionRiskReview,
    ManualPositionRiskReviewRequest,
    ManualRiskRecommendedAction,
    ModelInference,
    OriginalThesisStatus,
    VerifiedFact,
)


@dataclass(frozen=True)
class HighRiskModelReview:
    decision: str
    assessed_risk_level: str
    confidence: float
    finding_count: int
    call_ids: tuple[str, ...]


class HighRiskReviewer(Protocol):
    async def review(
        self,
        *,
        symbol: str,
        position: ManualPosition,
        decision_packet: DecisionPacket | None,
        risk_level: ManualPositionRiskLevel,
        triggered_rules: list[str],
        evidence: tuple[PersistedResearchRecord, ...],
    ) -> HighRiskModelReview: ...


class RouterHighRiskReviewer:
    """Use the risk-controller role for advisory review only."""

    def __init__(
        self,
        invocation_service: RouterInvocationService | None = None,
    ) -> None:
        self._invocation_service = invocation_service or RouterInvocationService()

    async def review(
        self,
        *,
        symbol: str,
        position: ManualPosition,
        decision_packet: DecisionPacket | None,
        risk_level: ManualPositionRiskLevel,
        triggered_rules: list[str],
        evidence: tuple[PersistedResearchRecord, ...],
    ) -> HighRiskModelReview:
        evidence_keys = {
            "realtime_quote": (
                "quote_type",
                "price",
                "previous_close",
                "pct_chg",
            ),
            "announcement": ("title", "category", "announcement_date"),
            "finance_news": ("title", "publisher", "published_at"),
            "financial_statement": (
                "statement_type",
                "report_period",
            ),
        }
        evidence_summary = [
            {
                "record_id": record.record_id,
                "data_type": record.data_type,
                "event_time": record.event_time.isoformat(),
                "source_level": record.source_level,
                "verified": record.verified,
                "data": {
                    key: record.payload.get(key)
                    for key in evidence_keys.get(record.data_type, ())
                },
            }
            for record in evidence
        ]
        packet_summary = (
            None
            if decision_packet is None
            else {
                "decision_id": decision_packet.decision_id,
                "decision_version": decision_packet.decision_version,
                "decision": decision_packet.decision.final_action.value,
                "risk_flags": decision_packet.risk_flags,
                "invalidation_conditions": decision_packet.invalidation_conditions,
                "missing_information": decision_packet.missing_information,
            }
        )
        prompt = json.dumps(
            {
                "task": (
                    "Review the evidence and deterministic risk findings for a "
                    "user-reported manual position. Return the risk-controller "
                    "JSON contract. This is advisory analysis only. Do not emit "
                    "executable trading instructions, transaction objects, "
                    "external-client actions, or automation steps."
                ),
                "allowed_outcome_meaning": (
                    "approve means continue observation; revise means human "
                    "review or consider reducing; reject means consider exiting"
                ),
                "position": position.model_dump(mode="json"),
                "decision_packet": packet_summary,
                "deterministic_risk_level": risk_level.value,
                "triggered_rules": triggered_rules,
                "evidence": evidence_summary,
            },
            ensure_ascii=False,
            default=str,
        )
        prompt = prompt[:48_000]
        response = await self._invocation_service.invoke(
            RouterInvokeRequest(
                role="risk_controller",
                symbol=symbol,
                prompt=prompt,
                risk_level=RiskLevel.HIGH,
                temperature=0,
                max_tokens=768,
            )
        )
        output = RiskReviewOutput.model_validate_json(response.content)
        return HighRiskModelReview(
            decision=output.decision.value,
            assessed_risk_level=output.assessed_risk_level.value,
            confidence=output.confidence,
            finding_count=len(output.findings),
            call_ids=tuple(response.call_ids),
        )


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.astimezone()


def _text(record: PersistedResearchRecord) -> str:
    return " ".join(str(value) for value in record.payload.values()).lower()


def _finite_float(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


class ManualPositionRiskReviewService:
    """Append advisory reviews without any ledger or simulation write capability."""

    _MATERIAL_KEYWORDS = (
        "退市",
        "立案",
        "调查",
        "处罚",
        "诉讼",
        "冻结",
        "破产",
        "违约",
        "减持",
        "亏损",
        "风险警示",
        "delist",
        "investigation",
        "penalty",
        "default",
        "bankruptcy",
    )
    _SEVERE_KEYWORDS = (
        "退市",
        "破产",
        "重大违法",
        "强制退市",
        "delist",
        "bankruptcy",
    )
    _SEVERITY = {
        ManualPositionRiskLevel.LOW: 0,
        ManualPositionRiskLevel.MEDIUM: 1,
        ManualPositionRiskLevel.HIGH: 2,
        ManualPositionRiskLevel.CRITICAL: 3,
    }

    def __init__(
        self,
        *,
        manual_trades: ManualTradeReadRepository,
        decisions: DecisionReadRepository,
        reviews: ManualPositionRiskReviewRepository,
        research: PositionResearchReader,
        high_risk_reviewer: HighRiskReviewer | None = None,
    ) -> None:
        self._manual_trades = manual_trades
        self._decisions = decisions
        self._reviews = reviews
        self._research = research
        self._high_risk_reviewer = high_risk_reviewer

    @classmethod
    def _raise_level(
        cls,
        current: ManualPositionRiskLevel,
        proposed: ManualPositionRiskLevel,
    ) -> ManualPositionRiskLevel:
        return (
            proposed
            if cls._SEVERITY[proposed] > cls._SEVERITY[current]
            else current
        )

    @staticmethod
    def _associated_packet(
        position: ManualPosition,
        manual_trades: ManualTradeReadRepository,
        decisions: DecisionReadRepository,
        missing_information: list[str],
    ) -> DecisionPacket | None:
        trades = manual_trades.list_trades(
            portfolio_id=position.portfolio_id,
            symbol=position.symbol,
        )
        linked = [trade for trade in trades if trade.decision_id]
        if not linked:
            missing_information.append(
                "No DecisionPacket is linked to the manual position history"
            )
            return None
        latest = max(linked, key=lambda trade: (trade.traded_at, trade.created_at))
        try:
            return decisions.get_latest(str(latest.decision_id))
        except Exception as exc:
            missing_information.append(
                "Linked DecisionPacket could not be read: "
                f"{type(exc).__name__}: {str(exc)[:240]}"
            )
            return None

    @staticmethod
    def _verified_facts(
        records: tuple[PersistedResearchRecord, ...],
    ) -> list[VerifiedFact]:
        facts: list[VerifiedFact] = []
        for record in records:
            if not record.verified:
                continue
            if record.data_type == "realtime_quote":
                value: object = {
                    "price": record.payload.get("price"),
                    "quote_type": record.payload.get("quote_type"),
                }
                fact = "Latest verified market price record"
            elif record.data_type == "announcement":
                value = {
                    "title": record.payload.get("title"),
                    "category": record.payload.get("category"),
                }
                fact = "Official company announcement"
            elif record.data_type == "financial_statement":
                value = {
                    "statement_type": record.payload.get("statement_type"),
                    "report_period": record.payload.get("report_period"),
                }
                fact = "Verified financial statement record"
            else:
                value = {
                    "title": record.payload.get("title"),
                    "publisher": record.payload.get("publisher"),
                }
                fact = "Verified research record"
            facts.append(
                VerifiedFact(
                    fact=fact,
                    value=value,
                    as_of=_as_aware(record.event_time),
                    source_record_ids=[record.record_id],
                )
            )
        return facts

    @classmethod
    def _deterministic_review(
        cls,
        *,
        position: ManualPosition,
        records: tuple[PersistedResearchRecord, ...],
        reviewed_at: datetime,
        missing_information: list[str],
    ) -> tuple[ManualPositionRiskLevel, list[str]]:
        level = ManualPositionRiskLevel.LOW
        rules: list[str] = []
        quote = next(
            (
                record
                for record in reversed(records)
                if record.data_type == "realtime_quote"
            ),
            None,
        )
        if quote is None:
            rules.append("QUOTE_MISSING")
            level = cls._raise_level(level, ManualPositionRiskLevel.HIGH)
        else:
            age = reviewed_at - _as_aware(quote.event_time)
            if age > timedelta(days=4):
                rules.append("QUOTE_STALE_OVER_4_DAYS")
                level = cls._raise_level(level, ManualPositionRiskLevel.HIGH)
            price = _finite_float(quote.payload.get("price"))
            if price is None or price <= 0:
                rules.append("QUOTE_PRICE_INVALID")
                level = cls._raise_level(level, ManualPositionRiskLevel.HIGH)
            elif position.quantity != 0 and position.average_cost > 0:
                return_pct = (price / position.average_cost) - 1
                if return_pct <= -0.15:
                    rules.append("MARK_TO_MARKET_LOSS_AT_LEAST_15_PERCENT")
                    level = cls._raise_level(
                        level,
                        ManualPositionRiskLevel.CRITICAL,
                    )
                elif return_pct <= -0.07:
                    rules.append("MARK_TO_MARKET_LOSS_AT_LEAST_7_PERCENT")
                    level = cls._raise_level(level, ManualPositionRiskLevel.HIGH)

        if position.quantity < 0:
            rules.append("MANUAL_POSITION_IS_SHORT")
            level = cls._raise_level(level, ManualPositionRiskLevel.HIGH)
        if position.quantity == 0:
            rules.append("MANUAL_POSITION_IS_FLAT")

        announcement_records = [
            record for record in records if record.data_type == "announcement"
        ]
        for record in announcement_records:
            text = _text(record)
            if any(keyword in text for keyword in cls._SEVERE_KEYWORDS):
                rules.append(
                    f"SEVERE_ANNOUNCEMENT_KEYWORD:{record.record_id}"
                )
                level = cls._raise_level(
                    level,
                    ManualPositionRiskLevel.CRITICAL,
                )
            elif any(keyword in text for keyword in cls._MATERIAL_KEYWORDS):
                rules.append(
                    f"MATERIAL_ANNOUNCEMENT_KEYWORD:{record.record_id}"
                )
                level = cls._raise_level(level, ManualPositionRiskLevel.HIGH)

        available_types = {record.data_type for record in records}
        if "financial_statement" not in available_types:
            rules.append("FINANCIAL_STATEMENT_MISSING")
            level = cls._raise_level(level, ManualPositionRiskLevel.MEDIUM)
        if "announcement" not in available_types:
            rules.append("ANNOUNCEMENT_DATA_MISSING")
            level = cls._raise_level(level, ManualPositionRiskLevel.MEDIUM)
        if "finance_news" not in available_types:
            rules.append("NEWS_DATA_MISSING")
            level = cls._raise_level(level, ManualPositionRiskLevel.MEDIUM)
        elif not any(
            record.verified
            for record in records
            if record.data_type == "finance_news"
        ):
            missing_information.append(
                "News records are media evidence and are not verified facts"
            )

        return level, list(dict.fromkeys(rules))

    @staticmethod
    def _thesis_status(
        packet: DecisionPacket | None,
        level: ManualPositionRiskLevel,
    ) -> OriginalThesisStatus:
        if packet is None:
            return OriginalThesisStatus.UNKNOWN
        if packet.decision.final_action.value == "veto":
            return OriginalThesisStatus.INVALIDATED
        if level == ManualPositionRiskLevel.CRITICAL:
            return OriginalThesisStatus.INVALIDATED
        if level == ManualPositionRiskLevel.HIGH:
            return OriginalThesisStatus.WEAKENED
        return OriginalThesisStatus.CONSISTENT

    @staticmethod
    def _recommended_action(
        *,
        level: ManualPositionRiskLevel,
        thesis_status: OriginalThesisStatus,
        model_review: HighRiskModelReview | None,
        model_failed: bool,
    ) -> ManualRiskRecommendedAction:
        if model_failed:
            return ManualRiskRecommendedAction.HUMAN_REVIEW_REQUIRED
        if (
            thesis_status == OriginalThesisStatus.INVALIDATED
            or level == ManualPositionRiskLevel.CRITICAL
            or (model_review is not None and model_review.decision == "reject")
        ):
            return ManualRiskRecommendedAction.CONSIDER_EXITING
        if (
            level == ManualPositionRiskLevel.HIGH
            or (model_review is not None and model_review.decision == "revise")
        ):
            return ManualRiskRecommendedAction.CONSIDER_REDUCING
        if level == ManualPositionRiskLevel.MEDIUM:
            return ManualRiskRecommendedAction.HUMAN_REVIEW_REQUIRED
        return ManualRiskRecommendedAction.CONTINUE_OBSERVATION

    async def review_position(
        self,
        position_id: str,
        request: ManualPositionRiskReviewRequest,
        *,
        created_by: str,
        channel: str,
    ) -> ManualPositionRiskReview:
        reviewed_at = datetime.now().astimezone()
        position = self._manual_trades.get_position(position_id)
        missing_information: list[str] = []
        packet = self._associated_packet(
            position,
            self._manual_trades,
            self._decisions,
            missing_information,
        )
        snapshot = await self._research.refresh(
            symbol=position.symbol,
            reviewed_at=reviewed_at,
            lookback_days=request.lookback_days,
        )
        missing_information.extend(snapshot.missing_information)
        records = tuple(
            record
            for record in snapshot.records
            if _as_aware(record.event_time) <= reviewed_at
        )
        risk_level, triggered_rules = self._deterministic_review(
            position=position,
            records=records,
            reviewed_at=reviewed_at,
            missing_information=missing_information,
        )
        thesis_status = self._thesis_status(packet, risk_level)

        model_review: HighRiskModelReview | None = None
        model_failed = False
        model_inferences: list[ModelInference] = []
        model_call_ids: list[str] = []
        if (
            request.allow_model_review
            and risk_level
            in {
                ManualPositionRiskLevel.HIGH,
                ManualPositionRiskLevel.CRITICAL,
            }
        ):
            if self._high_risk_reviewer is None:
                model_failed = True
                missing_information.append(
                    "High-risk model reviewer is unavailable; human review is required"
                )
            else:
                try:
                    model_review = await self._high_risk_reviewer.review(
                        symbol=position.symbol,
                        position=position,
                        decision_packet=packet,
                        risk_level=risk_level,
                        triggered_rules=triggered_rules,
                        evidence=records,
                    )
                    model_call_ids = list(dict.fromkeys(model_review.call_ids))
                    model_inferences.append(
                        ModelInference(
                            agent_role="risk_controller",
                            inference=(
                                "Advisory risk review: "
                                f"decision={model_review.decision}; "
                                f"assessed_level={model_review.assessed_risk_level}; "
                                f"finding_count={model_review.finding_count}."
                            ),
                            confidence=model_review.confidence,
                            supporting_source_record_ids=[
                                record.record_id for record in records
                            ],
                        )
                    )
                except Exception as exc:
                    model_failed = True
                    missing_information.append(
                        "High-risk model review failed: "
                        f"{type(exc).__name__}: {str(exc)[:300]}"
                    )

        candidates = [
            _as_aware(position.last_traded_at),
            *(_as_aware(record.event_time) for record in records),
        ]
        if packet is not None:
            candidates.append(_as_aware(packet.data_cutoff_time))
        eligible = [candidate for candidate in candidates if candidate <= reviewed_at]
        data_cutoff_time = max(eligible) if eligible else reviewed_at
        evidence_record_ids = list(
            dict.fromkeys(record.record_id for record in records)
        )
        review = ManualPositionRiskReview(
            review_id=uuid4().hex,
            position_id=position.position_id,
            reviewed_at=reviewed_at,
            data_cutoff_time=data_cutoff_time,
            risk_level=risk_level,
            original_thesis_status=thesis_status,
            triggered_rules=triggered_rules,
            new_verified_facts=self._verified_facts(records),
            model_inferences=model_inferences,
            missing_information=list(dict.fromkeys(missing_information)),
            evidence_record_ids=evidence_record_ids,
            model_call_ids=model_call_ids,
            recommended_action=self._recommended_action(
                level=risk_level,
                thesis_status=thesis_status,
                model_review=model_review,
                model_failed=model_failed,
            ),
        )
        return self._reviews.append(
            review,
            created_by=created_by,
            channel=channel,
        )

    def list_reviews(self, position_id: str) -> list[ManualPositionRiskReview]:
        self._manual_trades.get_position(position_id)
        return self._reviews.list_for_position(position_id)

    def latest_review(self, position_id: str) -> ManualPositionRiskReview:
        self._manual_trades.get_position(position_id)
        return self._reviews.latest_for_position(position_id)
