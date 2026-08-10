from __future__ import annotations

from datetime import datetime
from typing import Any

from data_hub.schemas.unified import FactorOutput, FactorType
from trading.research.capital_flow.models import MarketBar
from trading.research.capital_flow.service import CapitalFlowAnalysisService
from trading.research.orchestration.availability import (
    availability_weight,
    classify_availability,
)
from trading.research.orchestration.evidence_graph import build_evidence_graph
from trading.research.orchestration.models import (
    BASE_FACTOR_TYPES,
    ORCHESTRATION_ALGORITHM_VERSION,
)
from trading.research.orchestration.repository import (
    BulkFactorData,
    OrchestrationRepository,
)
from trading.research.orchestration.schemas import (
    FactorAvailabilityStatus,
    FactorBundle,
    FactorCoverage,
    FactorView,
    OrchestrationRiskFlag,
)
from trading.research.orchestration.shadow_scorer import stable_hash
from trading.research.technical.analysis import technical_signal
from trading.schemas import AnalysisMode, Bar


_FIELD_NAMES = {
    FactorType.TECHNICAL: "technical",
    FactorType.FUNDAMENTAL: "fundamental",
    FactorType.SENTIMENT: "sentiment",
    FactorType.POLICY_NEWS: "policy_news",
    FactorType.CAPITAL_FLOW: "capital_flow",
}


def _factor_id(
    symbol: str,
    factor_type: FactorType,
    snapshot_hash: str,
) -> str:
    return "fac_" + stable_hash(
        {
            "symbol": symbol,
            "factor_type": factor_type.value,
            "snapshot_hash": snapshot_hash,
            "version": ORCHESTRATION_ALGORITHM_VERSION,
        }
    )[:32]


class FactorLoader:
    def __init__(self, repository: OrchestrationRepository) -> None:
        self.repository = repository

    @staticmethod
    def _with_availability(
        factor: FactorView,
        *,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
    ) -> FactorView:
        status, reason = classify_availability(
            factor,
            analysis_mode=analysis_mode,
            requested_cutoff=data_cutoff,
        )
        return factor.model_copy(
            update={
                "availability_status": status,
                "availability_reason": reason,
            }
        )

    def _from_factor_output(
        self,
        factor: FactorOutput,
        *,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
    ) -> FactorView:
        event_ids = [
            item for item in factor.evidence_ids if item.startswith("evt_")
        ]
        market_ids = [
            item
            for item in factor.evidence_ids
            if item.startswith(("hbar_", "bar_", "cmr_"))
        ]
        view = FactorView(
            factor_output_id=factor.factor_id,
            factor_type=factor.factor_type,
            score=factor.score,
            confidence=factor.confidence,
            shadow_mode=factor.shadow_mode,
            generated_at=factor.generated_at,
            data_cutoff=factor.data_cutoff,
            evidence_ids=factor.evidence_ids,
            risk_flags=factor.risk_flags,
            algorithm_version=factor.algorithm_version,
            input_snapshot_hash=factor.input_snapshot_hash,
            availability_status=FactorAvailabilityStatus.AVAILABLE,
            availability_reason="pending availability classification",
            event_cluster_ids=event_ids,
            market_record_ids=market_ids,
            source_factor_output_id=factor.factor_id,
            metadata=factor.metadata,
        )
        return self._with_availability(
            view,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
        )

    def _technical(
        self,
        symbol: str,
        bars: list[MarketBar],
        *,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
        generated_at: datetime,
    ) -> FactorView | None:
        valid = [
            bar
            for bar in bars
            if None
            not in (
                bar.open,
                bar.high,
                bar.low,
                bar.close,
                bar.volume,
            )
        ]
        if len(valid) < 30:
            return None
        converted = [
            Bar(
                trade_date=bar.event_time.astimezone().date(),
                open=float(bar.open),
                high=float(bar.high),
                low=float(bar.low),
                close=float(bar.close),
                volume=float(bar.volume),
            )
            for bar in valid
        ]
        signal = technical_signal(converted)
        evidence = [bar.canonical_record_id for bar in valid[-60:]]
        snapshot_hash = stable_hash(
            {
                "symbol": symbol,
                "bar_ids": evidence,
                "cutoff": data_cutoff.isoformat(),
                "version": "technical-screening-v1",
            }
        )
        view = FactorView(
            factor_output_id=_factor_id(
                symbol,
                FactorType.TECHNICAL,
                snapshot_hash,
            ),
            factor_type=FactorType.TECHNICAL,
            score=signal.score,
            confidence=signal.confidence,
            shadow_mode=True,
            generated_at=generated_at,
            data_cutoff=data_cutoff,
            evidence_ids=evidence,
            risk_flags=signal.risks,
            algorithm_version="technical-screening-v1",
            input_snapshot_hash=snapshot_hash,
            availability_status=FactorAvailabilityStatus.AVAILABLE,
            availability_reason="pending availability classification",
            market_record_ids=evidence,
            metadata={
                "structured_summary": signal.summary,
                "local_only": True,
            },
        )
        return self._with_availability(
            view,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
        )

    def _capital_flow_candidates(
        self,
        data: BulkFactorData,
        *,
        data_cutoff: datetime,
    ) -> dict[str, Any]:
        candidates = CapitalFlowAnalysisService.screen_candidates(
            bars_by_symbol=data.bars_by_symbol,
            data_cutoff=data_cutoff,
            symbols=data.symbols,
        )
        return {candidate.symbol: candidate for candidate in candidates}

    def _capital_flow(
        self,
        symbol: str,
        candidate: Any | None,
        *,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
        generated_at: datetime,
    ) -> FactorView | None:
        if candidate is None:
            return None
        risk_flags = [
            flag.value if hasattr(flag, "value") else str(flag)
            for flag in candidate.risk_flags
        ]
        snapshot_hash = stable_hash(
            {
                "symbol": symbol,
                "candidate_id": candidate.snapshot_id,
                "cutoff": data_cutoff.isoformat(),
                "version": "capital-flow-screening-v1",
            }
        )
        view = FactorView(
            factor_output_id=_factor_id(
                symbol,
                FactorType.CAPITAL_FLOW,
                snapshot_hash,
            ),
            factor_type=FactorType.CAPITAL_FLOW,
            score=candidate.score,
            confidence=candidate.confidence,
            shadow_mode=True,
            generated_at=generated_at,
            data_cutoff=data_cutoff,
            evidence_ids=candidate.evidence_ids,
            risk_flags=risk_flags,
            algorithm_version="capital-flow-screening-v1",
            input_snapshot_hash=snapshot_hash,
            availability_status=FactorAvailabilityStatus.PARTIAL,
            availability_reason="pending availability classification",
            market_record_ids=candidate.evidence_ids,
            metadata={
                "missing_fields": candidate.missing_fields,
                "local_only": True,
            },
        )
        return self._with_availability(
            view,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
        )

    def _snapshot_factor(
        self,
        snapshot: dict[str, object] | None,
        *,
        factor_type: FactorType,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
    ) -> FactorView | None:
        if snapshot is None:
            return None
        evidence_ids = list(snapshot["evidence_ids"])
        view = FactorView(
            factor_output_id=str(snapshot["snapshot_id"]),
            factor_type=factor_type,
            score=float(snapshot["score"]),
            confidence=float(snapshot["confidence"]),
            shadow_mode=bool(snapshot["shadow_mode"]),
            generated_at=snapshot["generated_at"],
            data_cutoff=snapshot["data_cutoff"],
            evidence_ids=evidence_ids,
            risk_flags=list(snapshot["risk_flags"]),
            algorithm_version=str(snapshot["algorithm_version"]),
            input_snapshot_hash=str(snapshot["input_snapshot_hash"]),
            availability_status=FactorAvailabilityStatus.AVAILABLE,
            availability_reason="pending availability classification",
            event_cluster_ids=[
                item for item in evidence_ids if item.startswith("evt_")
            ],
            metadata={
                "source_snapshot_id": snapshot["snapshot_id"],
                "shared_sentiment_event_ids": snapshot.get(
                    "shared_sentiment_event_ids",
                    [],
                ),
            },
        )
        return self._with_availability(
            view,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
        )

    def build_bundle(
        self,
        *,
        symbol: str,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
        factors: dict[FactorType, FactorView],
    ) -> FactorBundle:
        groups, shared_clusters = build_evidence_graph(factors)
        available = [
            factor_type
            for factor_type, factor in factors.items()
            if availability_weight(
                factor.availability_status,
                analysis_mode=analysis_mode,
            )
            > 0
        ]
        missing = [
            factor_type
            for factor_type in BASE_FACTOR_TYPES
            if factor_type not in factors
        ]
        stale = [
            factor_type
            for factor_type, factor in factors.items()
            if factor.availability_status == FactorAvailabilityStatus.STALE
        ]
        conflicting = [
            factor_type
            for factor_type, factor in factors.items()
            if factor.availability_status == FactorAvailabilityStatus.CONFLICT
        ]
        risk_flags: list[OrchestrationRiskFlag] = []
        if missing:
            risk_flags.append(OrchestrationRiskFlag.FACTOR_DATA_GAP)
        if len(available) < 3:
            risk_flags.append(OrchestrationRiskFlag.LOW_FACTOR_COVERAGE)
        if stale:
            risk_flags.append(OrchestrationRiskFlag.STALE_FACTOR)
        if conflicting:
            risk_flags.append(OrchestrationRiskFlag.FACTOR_CONFLICT)
        if groups:
            risk_flags.append(OrchestrationRiskFlag.SHARED_EVIDENCE)
        if any(
            factor.availability_status
            == FactorAvailabilityStatus.FUTURE_DATA_REJECTED
            for factor in factors.values()
        ):
            risk_flags.append(OrchestrationRiskFlag.FUTURE_FACTOR_REJECTED)
        if any(
            factor.availability_status == FactorAvailabilityStatus.UNVERIFIED
            for factor in factors.values()
        ):
            risk_flags.append(OrchestrationRiskFlag.UNVERIFIED_FACTOR)
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for factor in factors.values()
                for evidence_id in factor.evidence_ids
            )
        )
        coverage = FactorCoverage(
            available_count=len(available),
            coverage_ratio=len(available) / 5,
            display=f"{len(available)}/5",
        )
        snapshot_hash = stable_hash(
            {
                "symbol": symbol,
                "mode": analysis_mode.value,
                "data_cutoff": data_cutoff.isoformat(),
                "factors": {
                    key.value: {
                        "id": factor.factor_output_id,
                        "status": factor.availability_status.value,
                        "hash": factor.input_snapshot_hash,
                    }
                    for key, factor in sorted(
                        factors.items(),
                        key=lambda item: item[0].value,
                    )
                },
                "shared_evidence_groups": [
                    group.model_dump(mode="json") for group in groups
                ],
                "version": ORCHESTRATION_ALGORITHM_VERSION,
            }
        )
        payload = {
            _FIELD_NAMES[factor_type]: factor
            for factor_type, factor in factors.items()
        }
        return FactorBundle(
            symbol=symbol,
            analysis_mode=analysis_mode,
            data_cutoff=data_cutoff,
            **payload,
            available_factor_types=sorted(
                available,
                key=lambda item: item.value,
            ),
            missing_factor_types=missing,
            stale_factor_types=stale,
            conflicting_factor_types=conflicting,
            evidence_ids=evidence_ids,
            shared_event_cluster_ids=shared_clusters,
            shared_evidence_groups=groups,
            risk_flags=list(dict.fromkeys(risk_flags)),
            factor_coverage=coverage,
            input_snapshot_hash=snapshot_hash,
        )

    def load_many(
        self,
        *,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
        symbols: list[str] | None = None,
    ) -> tuple[list[FactorBundle], int]:
        data = self.repository.load_bulk(
            data_cutoff=data_cutoff,
            symbols=symbols,
        )
        generated_at = datetime.now().astimezone()
        capital_candidates = self._capital_flow_candidates(
            data,
            data_cutoff=data_cutoff,
        )
        bundles: list[FactorBundle] = []
        for symbol in data.symbols:
            factors: dict[FactorType, FactorView] = {}
            stored = data.factor_outputs.get(symbol, {})
            fundamental = stored.get(FactorType.FUNDAMENTAL)
            if fundamental is not None:
                factors[FactorType.FUNDAMENTAL] = self._from_factor_output(
                    fundamental,
                    analysis_mode=analysis_mode,
                    data_cutoff=data_cutoff,
                )
            technical = self._technical(
                symbol,
                data.bars_by_symbol.get(symbol, []),
                analysis_mode=analysis_mode,
                data_cutoff=data_cutoff,
                generated_at=generated_at,
            )
            if technical is not None:
                factors[FactorType.TECHNICAL] = technical
            capital = self._capital_flow(
                symbol,
                capital_candidates.get(symbol),
                analysis_mode=analysis_mode,
                data_cutoff=data_cutoff,
                generated_at=generated_at,
            )
            if capital is not None:
                factors[FactorType.CAPITAL_FLOW] = capital
            sentiment = self._snapshot_factor(
                data.sentiment_snapshots.get(symbol),
                factor_type=FactorType.SENTIMENT,
                analysis_mode=analysis_mode,
                data_cutoff=data_cutoff,
            )
            if sentiment is not None:
                factors[FactorType.SENTIMENT] = sentiment
            policy = self._snapshot_factor(
                data.policy_news_snapshots.get(symbol),
                factor_type=FactorType.POLICY_NEWS,
                analysis_mode=analysis_mode,
                data_cutoff=data_cutoff,
            )
            if policy is not None:
                factors[FactorType.POLICY_NEWS] = policy
            bundles.append(
                self.build_bundle(
                    symbol=symbol,
                    analysis_mode=analysis_mode,
                    data_cutoff=data_cutoff,
                    factors=factors,
                )
            )
        return bundles, data.database_connection_count

    @staticmethod
    def persistable_factor(
        symbol: str,
        factor: FactorView,
    ) -> FactorOutput:
        if factor.source_factor_output_id is not None:
            raise ValueError("factor is already backed by a FactorOutput")
        if not factor.evidence_ids:
            raise ValueError("factor requires traceable evidence")
        return FactorOutput(
            factor_id=(
                factor.factor_output_id
                if factor.factor_output_id.startswith("fac_")
                else _factor_id(
                    symbol,
                    factor.factor_type,
                    factor.input_snapshot_hash,
                )
            ),
            symbol=symbol,
            factor_type=factor.factor_type,
            score=factor.score,
            confidence=factor.confidence,
            data_cutoff=factor.data_cutoff,
            generated_at=factor.generated_at,
            evidence_ids=factor.evidence_ids,
            risk_flags=factor.risk_flags,
            model_call_ids=[],
            algorithm_version=factor.algorithm_version,
            input_snapshot_hash=factor.input_snapshot_hash,
            shadow_mode=True,
            metadata={
                **factor.metadata,
                "availability_status": factor.availability_status.value,
                "availability_reason": factor.availability_reason,
                "formal_strategy_weight": 0.0,
            },
        )


__all__ = ["FactorLoader"]
