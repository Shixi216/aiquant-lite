from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from config.version import ROUTER_GENERATOR_VERSION
from data_hub.services.daily_bars_service import DailyBarsService
from trading.decision_support.adversarial_review import adversarial_review
from trading.decision_support.decision_packets import DecisionRepository
from trading.decision_support.risk_rules import evaluate_strategy_risk
from trading.research.fundamental import fundamental_signal
from trading.research.fundamental.models import (
    FundamentalAnalysisResult,
)
from trading.research.fundamental.service import (
    DecisionFundamentalService,
    FundamentalAnalysisService,
)
from trading.research.sentiment import (
    DecisionSentimentService,
    SentimentAnalysisService,
)
from trading.research.policy_news import (
    DecisionPolicyNewsService,
    PolicyNewsAnalysisService,
)
from trading.research.capital_flow.service import (
    CapitalFlowAnalysisService,
    DecisionCapitalFlowService,
)
from trading.research.technical import technical_signal
from trading.schemas import (
    Action,
    AnalysisMode,
    AdversarialReview,
    AgentOpinion,
    DecisionChallengeRequest,
    DecisionChallengeResult,
    DecisionFromDataRequest,
    DecisionPacket,
    DecisionRequest,
    DecisionScenario,
    DecisionStatus,
    DecisionTrace,
    FundamentalDataStatus,
    ModelInference,
    Signal,
    StrategyProposal,
    VerifiedFact,
)


def _stance(score: float) -> str:
    if score > 0.2:
        return "bullish"
    if score < -0.2:
        return "bearish"
    return "neutral"


def _opinion(signal: Signal) -> AgentOpinion:
    return AgentOpinion(
        role=signal.role,
        stance=_stance(signal.score),
        summary=signal.summary,
        evidence=signal.evidence,
        counterpoints=signal.risks,
        confidence=signal.confidence,
    )


def _generate_strategy(technical: Signal, fundamental: Signal) -> StrategyProposal:
    combined = technical.score * 0.6 + fundamental.score * 0.4
    confidence = technical.confidence * 0.6 + fundamental.confidence * 0.4
    if combined >= 0.2:
        action = Action.BUY
        target = min(0.20, max(0.02, combined * 0.20))
    elif combined <= -0.2:
        action = Action.SELL
        target = 0
    else:
        action = Action.HOLD
        target = 0
    return StrategyProposal(
        name="auditable_multi_factor_v1",
        action=action,
        target_weight=target,
        confidence=max(0, min(1, confidence)),
        entry_rule="Technical/fundamental weighted score >= 0.20 and all safety gates pass.",
        exit_rule="Score <= -0.20, stop loss, take profit, or risk veto.",
        invalidation_rules=[
            "stale or contradictory market data",
            "daily loss or exposure limit reached",
            "risk controller veto",
            "adversarial evidence-gap review",
        ],
        evidence=[*technical.evidence, *fundamental.evidence],
    )


class TradingDecisionService:
    """Run deterministic decision agents without any persistence dependency."""

    async def decide(self, request: DecisionRequest) -> DecisionTrace:
        technical, fundamental = await asyncio.gather(
            asyncio.to_thread(technical_signal, request.bars),
            asyncio.to_thread(fundamental_signal, request.fundamentals),
        )
        proposal = _generate_strategy(technical, fundamental)
        risk = evaluate_strategy_risk(proposal, request.portfolio, request.risk_limits)
        evidence = list(dict.fromkeys([*request.evidence_refs, *proposal.evidence]))
        review = adversarial_review(
            proposal,
            len(evidence),
            technical.confidence,
            fundamental.confidence,
        )
        opinions = [_opinion(technical), _opinion(fundamental)]
        opinions.append(
            AgentOpinion(
                role="strategy_agent",
                stance=_stance(technical.score * 0.6 + fundamental.score * 0.4),
                summary=f"Generated {proposal.name}: {proposal.action.value}.",
                evidence=proposal.evidence,
                counterpoints=proposal.invalidation_rules,
                confidence=proposal.confidence,
            )
        )
        if risk.vetoed:
            final_action = Action.VETO
            final_weight = 0
        elif not review.passed and proposal.action == Action.BUY:
            final_action = Action.HOLD
            final_weight = request.portfolio.current_weight
        else:
            final_action = proposal.action
            final_weight = risk.adjusted_target_weight
        summary = [
            technical.summary,
            fundamental.summary,
            f"Strategy proposal: {proposal.action.value} at target weight "
            f"{proposal.target_weight:.2%}.",
        ]
        summary.extend(f"Risk veto: {reason}" for reason in risk.reasons)
        summary.extend(f"Adversarial finding: {finding}" for finding in review.findings)
        return DecisionTrace(
            symbol=request.symbol,
            as_of=max(bar.trade_date for bar in request.bars),
            opinions=opinions,
            proposal=proposal,
            risk_verdict=risk,
            adversarial_review=review,
            final_action=final_action,
            final_target_weight=final_weight,
            rationale_summary=summary,
            evidence_refs=evidence,
        )


class DecisionService:
    """Create and query immutable DecisionPackets through one repository boundary."""

    def __init__(self, repository: DecisionRepository) -> None:
        self.repository = repository
        self.decision_engine = TradingDecisionService()
        self.daily_bars_service_factory = DailyBarsService
        self.fundamental_service = DecisionFundamentalService(
            FundamentalAnalysisService()
        )
        self.sentiment_service = DecisionSentimentService(
            SentimentAnalysisService()
        )
        self.policy_news_service = DecisionPolicyNewsService(
            PolicyNewsAnalysisService()
        )
        self.capital_flow_service = DecisionCapitalFlowService(
            CapitalFlowAnalysisService()
        )

    @staticmethod
    def _source_ids(evidence_refs: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                reference.split(":", 1)[1]
                for reference in evidence_refs
                if reference.startswith("data_record:") and ":" in reference
            )
        )

    @staticmethod
    def _model_call_ids(evidence_refs: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                reference.split(":", 1)[1]
                for reference in evidence_refs
                if reference.startswith("model_call:") and ":" in reference
            )
        )

    @staticmethod
    def _scenario(
        opinions: list[AgentOpinion],
        stance: str,
        fallback: str,
    ) -> DecisionScenario:
        matching = [opinion.summary for opinion in opinions if opinion.stance == stance]
        return DecisionScenario(
            summary=" ".join(matching) if matching else fallback,
            conditions=[
                counterpoint
                for opinion in opinions
                if opinion.stance == stance
                for counterpoint in opinion.counterpoints
            ],
        )

    def _build_packet(
        self,
        *,
        trace: DecisionTrace,
        source_record_ids: list[str],
        model_call_ids: list[str],
        factor_output_ids: list[str],
        generated_at: datetime,
        data_cutoff_time: datetime,
        decision_id: str,
        decision_version: int,
        supersedes_version: int | None,
        extra_risk_flags: list[str] | None = None,
        extra_disagreements: list[str] | None = None,
        fundamental_result: FundamentalAnalysisResult | None = None,
    ) -> DecisionPacket:
        records = self.repository.get_source_records(source_record_ids)
        verified_records = [record for record in records if record["verified"]]
        verified_facts = [
            VerifiedFact(
                fact=f"{record['data_type']}:{record['record_id']}",
                value=record["payload"],
                as_of=record["event_time"],
                source_record_ids=[record["record_id"]],
            )
            for record in verified_records
        ]
        inferences = [
            ModelInference(
                agent_role=opinion.role,
                inference=opinion.summary,
                confidence=opinion.confidence,
                supporting_source_record_ids=source_record_ids,
            )
            for opinion in trace.opinions
        ]
        stances = {opinion.stance for opinion in trace.opinions}
        disagreements = []
        if len(stances) > 1:
            disagreements.append(
                "Agent stances differ: " + ", ".join(sorted(stances))
            )
        disagreements.extend(extra_disagreements or [])
        risk_flags = list(
            dict.fromkeys(
                [
                    *trace.risk_verdict.reasons,
                    *trace.adversarial_review.findings,
                    *(extra_risk_flags or []),
                ]
            )
        )
        missing_information: list[str] = []
        if not verified_records:
            missing_information.append("No source record is independently verified")
        if any(opinion.role == "fundamental_agent" and opinion.confidence < 0.3 for opinion in trace.opinions):
            missing_information.append("Current fundamental confirmation is incomplete")
        latest_close = 0.0
        for record in records:
            value = record["payload"].get("close")
            if isinstance(value, (int, float)):
                latest_close = float(value)
        return DecisionPacket.create(
            decision_id=decision_id,
            decision_version=decision_version,
            supersedes_version=supersedes_version,
            symbol=trace.symbol,
            generated_at=generated_at,
            data_cutoff_time=data_cutoff_time,
            schema_version="1.0",
            generator_version=ROUTER_GENERATOR_VERSION,
            status=DecisionStatus.FINAL,
            decision=trace,
            confidence=trace.proposal.confidence,
            evidence_quality=len(verified_records) / len(records),
            verified_facts=verified_facts,
            calculated_metrics={
                "source_record_count": float(len(records)),
                "verified_source_count": float(len(verified_records)),
                "latest_close": latest_close,
                "suggested_target_weight": trace.final_target_weight,
            },
            model_inferences=inferences,
            assumptions=[
                "Signals use only records at or before data_cutoff_time.",
                "Calculated metrics are deterministic and separate from model inferences.",
            ],
            bull_case=self._scenario(
                trace.opinions,
                "bullish",
                "No agent supplied a bullish case.",
            ),
            base_case=DecisionScenario(
                summary=(
                    f"Base decision is {trace.final_action.value} with target weight "
                    f"{trace.final_target_weight:.2%}."
                ),
                conditions=trace.rationale_summary,
            ),
            bear_case=self._scenario(
                trace.opinions,
                "bearish",
                "No agent supplied a bearish case.",
            ),
            entry_conditions=[trace.proposal.entry_rule],
            invalidation_conditions=[
                trace.proposal.exit_rule,
                *trace.proposal.invalidation_rules,
            ],
            risk_flags=risk_flags,
            suggested_position_limit=trace.final_target_weight,
            holding_horizon="days_to_weeks",
            missing_information=missing_information,
            agent_disagreements=disagreements,
            source_record_ids=source_record_ids,
            factor_output_ids=factor_output_ids,
            model_call_ids=model_call_ids,
            analysis_mode=(
                fundamental_result.analysis_mode
                if fundamental_result is not None
                else AnalysisMode.DECISION
            ),
            fundamental_data_status=(
                fundamental_result.status
                if fundamental_result is not None
                else None
            ),
            used_report_period=(
                fundamental_result.used_report_period
                if fundamental_result is not None
                else None
            ),
            announcement_time=(
                fundamental_result.announcement_time
                if fundamental_result is not None
                else None
            ),
            data_available_time=(
                fundamental_result.data_available_time
                if fundamental_result is not None
                else None
            ),
            missing_fields=(
                fundamental_result.missing_fields
                if fundamental_result is not None
                else []
            ),
            factor_output_id=(
                fundamental_result.factor_output.factor_id
                if (
                    fundamental_result is not None
                    and fundamental_result.factor_output is not None
                )
                else None
            ),
            verification_status=(
                fundamental_result.verification_status
                if fundamental_result is not None
                else None
            ),
            auto_fetch_attempted=(
                fundamental_result.auto_fetch_attempted
                if fundamental_result is not None
                else False
            ),
            auto_fetch_result=(
                fundamental_result.auto_fetch_result
                if fundamental_result is not None
                else None
            ),
            manual_fundamental_audit_id=(
                fundamental_result.manual_input_id
                if fundamental_result is not None
                else None
            ),
        )

    async def create_decision(
        self,
        request: DecisionRequest,
        *,
        data_cutoff_time: datetime | None = None,
        fundamental_result: FundamentalAnalysisResult | None = None,
    ) -> DecisionPacket:
        source_record_ids = self._source_ids(request.evidence_refs)
        records = self.repository.get_source_records(source_record_ids)
        trace = await self.decision_engine.decide(request)
        generated_at = datetime.now().astimezone()
        inferred_cutoff = max(
            record["event_time"] for record in records
        )
        actual_cutoff = data_cutoff_time or inferred_cutoff
        if actual_cutoff > generated_at:
            raise ValueError(
                "data_cutoff_time must not be later than generation time"
            )
        if inferred_cutoff > actual_cutoff:
            raise ValueError(
                "source evidence must not be later than data_cutoff_time"
            )
        packet = self._build_packet(
            trace=trace,
            source_record_ids=source_record_ids,
            model_call_ids=self._model_call_ids(request.evidence_refs),
            factor_output_ids=request.factor_output_ids,
            generated_at=generated_at,
            data_cutoff_time=actual_cutoff,
            decision_id=trace.trace_id,
            decision_version=1,
            supersedes_version=None,
            extra_risk_flags=(
                [
                    flag.value
                    for flag in fundamental_result.risk_flags
                ]
                if fundamental_result is not None
                else None
            ),
            fundamental_result=fundamental_result,
        )
        return self.repository.insert(packet)

    @staticmethod
    def _decision_cutoff(
        request: DecisionFromDataRequest,
    ) -> datetime:
        now = datetime.now().astimezone()
        if request.data_cutoff is not None:
            return request.data_cutoff
        end_of_day = datetime.combine(
            request.end_date,
            datetime.max.time().replace(microsecond=0),
            tzinfo=ZoneInfo("Asia/Shanghai"),
        )
        return min(end_of_day, now)

    async def create_decision_from_data(
        self,
        request: DecisionFromDataRequest,
    ) -> DecisionPacket:
        if request.analysis_mode != AnalysisMode.DECISION:
            raise ValueError(
                "/v1/decisions/from-data creates immutable packets and "
                "therefore accepts DECISION mode only"
            )
        if not request.strict_point_in_time:
            raise ValueError(
                "DECISION requires strict_point_in_time=true"
            )
        data_cutoff_time = self._decision_cutoff(request)
        response = await asyncio.to_thread(
            self.daily_bars_service_factory().get_daily_bars,
            request.symbol,
            request.start_date.strftime("%Y%m%d"),
            request.end_date.strftime("%Y%m%d"),
        )
        bars: list[dict[str, object]] = []
        evidence_refs: list[str] = []
        for record in response.records:
            if record.event_time > data_cutoff_time:
                continue
            try:
                trade_date = datetime.strptime(
                    str(record.data["trade_date"]).replace("-", ""),
                    "%Y%m%d",
                ).date()
                bars.append(
                    {
                        "trade_date": trade_date,
                        "open": record.data["open"],
                        "high": record.data["high"],
                        "low": record.data["low"],
                        "close": record.data["close"],
                        "volume": record.data["volume"],
                    }
                )
                evidence_refs.append(f"data_record:{record.record_id}")
            except (KeyError, TypeError, ValueError):
                continue
        fundamental_result = await asyncio.to_thread(
            self.fundamental_service.analyze,
            symbol=response.symbol,
            data_cutoff=data_cutoff_time,
            auto_fetch=request.auto_fetch_fundamental,
            manual_snapshot=request.fundamentals,
            allow_manual_override=(
                request.allow_user_fundamental_override
            ),
            manual_override_reason=(
                request.user_fundamental_override_reason
            ),
            manual_operator_confirmed=(
                request.user_fundamental_operator_confirmed
            ),
        )
        evidence_refs.extend(
            f"data_record:{record_id}"
            for record_id in fundamental_result.source_record_ids
        )
        factor_output_ids = list(request.factor_output_ids)
        if fundamental_result.factor_output is not None:
            factor_output_ids.append(
                fundamental_result.factor_output.factor_id
            )
        sentiment_result = await self.sentiment_service.analyze(
            symbol=response.symbol,
            data_cutoff=data_cutoff_time,
            allow_model_calls=False,
        )
        if sentiment_result.factor_output is not None:
            factor_output_ids.append(
                sentiment_result.factor_output.factor_id
            )
        policy_news_result = await self.policy_news_service.analyze(
            symbol=response.symbol,
            data_cutoff=data_cutoff_time,
            allow_model_calls=False,
        )
        if policy_news_result.factor_output is not None:
            factor_output_ids.append(
                policy_news_result.factor_output.factor_id
            )
        capital_flow_factor = await self.capital_flow_service.analyze(
            symbol=response.symbol,
            data_cutoff=data_cutoff_time,
        )
        if capital_flow_factor is not None:
            factor_output_ids.append(capital_flow_factor.factor_id)
        use_automatic_snapshot = (
            fundamental_result.status
            not in {
                FundamentalDataStatus.UNAVAILABLE,
                FundamentalDataStatus.CONFLICT,
            }
            or fundamental_result.manual_input_id is not None
            and request.allow_user_fundamental_override
        )
        try:
            decision_request = DecisionRequest(
                symbol=response.symbol,
                bars=bars,
                fundamentals=(
                    fundamental_result.snapshot
                    if use_automatic_snapshot
                    else None
                ),
                portfolio=request.portfolio,
                risk_limits=request.risk_limits,
                evidence_refs=evidence_refs,
                factor_output_ids=list(
                    dict.fromkeys(factor_output_ids)
                ),
            )
        except ValueError as exc:
            raise ValueError(
                f"At least 30 valid daily bars are required: {exc}"
            ) from exc
        return await self.create_decision(
            decision_request,
            data_cutoff_time=data_cutoff_time,
            fundamental_result=fundamental_result,
        )

    def get_latest(self, decision_id: str) -> DecisionPacket:
        return self.repository.get_latest(decision_id)

    def list_versions(self, decision_id: str) -> list[DecisionPacket]:
        return self.repository.list_versions(decision_id)

    def get_version(self, decision_id: str, version: int) -> DecisionPacket:
        return self.repository.get(decision_id, version)

    def challenge(
        self,
        decision_id: str,
        request: DecisionChallengeRequest,
    ) -> DecisionChallengeResult:
        challenged = self.repository.get_latest(decision_id)
        created_at = datetime.now().astimezone()
        source_record_ids = list(
            dict.fromkeys(
                [*challenged.source_record_ids, *request.source_record_ids]
            )
        )
        records = self.repository.get_source_records(source_record_ids)
        model_call_ids = list(
            dict.fromkeys([*challenged.model_call_ids, *request.model_call_ids])
        )
        inferred_cutoff = max(
            [challenged.data_cutoff_time, *(record["event_time"] for record in records)]
        )
        data_cutoff_time = request.data_cutoff_time or inferred_cutoff
        if data_cutoff_time > created_at:
            raise ValueError("data_cutoff_time must not be later than challenge time")
        if data_cutoff_time < inferred_cutoff:
            raise ValueError(
                "data_cutoff_time must include every referenced source record"
            )
        review = AdversarialReview(
            passed=False,
            findings=[f"Challenge raised: {request.challenge}"],
            required_actions=[
                "Re-evaluate the challenged conclusion before relying on it."
            ],
        )
        challenge_id = uuid4().hex
        new_packet: DecisionPacket | None = None
        if request.create_new_version:
            next_version = challenged.decision_version + 1
            old_trace = challenged.decision
            new_action = (
                Action.HOLD
                if old_trace.final_action == Action.BUY
                else old_trace.final_action
            )
            new_weight = (
                0.0
                if old_trace.final_action == Action.BUY
                else old_trace.final_target_weight
            )
            new_trace = old_trace.model_copy(
                update={
                    "trace_id": f"{decision_id}:v{next_version}",
                    "created_at": created_at,
                    "adversarial_review": review,
                    "final_action": new_action,
                    "final_target_weight": new_weight,
                    "rationale_summary": [
                        *old_trace.rationale_summary,
                        f"Challenge: {request.challenge}",
                    ],
                }
            )
            new_packet = self._build_packet(
                trace=new_trace,
                source_record_ids=source_record_ids,
                model_call_ids=model_call_ids,
                factor_output_ids=challenged.factor_output_ids,
                generated_at=created_at,
                data_cutoff_time=data_cutoff_time,
                decision_id=decision_id,
                decision_version=next_version,
                supersedes_version=challenged.decision_version,
                extra_risk_flags=["Decision challenged; re-analysis created a new version"],
                extra_disagreements=[request.challenge],
            )
            self.repository.insert(new_packet)
        self.repository.record_challenge(
            challenge_id=challenge_id,
            decision_id=decision_id,
            decision_version=challenged.decision_version,
            challenge_text=request.challenge,
            review=review,
            data_cutoff_time=data_cutoff_time,
            source_record_ids=source_record_ids,
            model_call_ids=model_call_ids,
            created_version=(
                new_packet.decision_version if new_packet is not None else None
            ),
            created_at=created_at,
        )
        return DecisionChallengeResult(
            challenge_id=challenge_id,
            decision_id=decision_id,
            challenged_version=challenged.decision_version,
            adversarial_review=review,
            created_version=(
                new_packet.decision_version if new_packet is not None else None
            ),
            new_packet=new_packet,
            data_cutoff_time=data_cutoff_time,
            source_record_ids=source_record_ids,
            model_call_ids=model_call_ids,
            created_at=created_at,
        )
