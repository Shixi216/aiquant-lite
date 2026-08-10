from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime

from config.settings import settings
from data_hub.repositories import FactorOutputRepository
from data_hub.schemas.unified import FactorOutput, FactorType
from trading.research.sentiment.aggregator import aggregate_symbol
from trading.research.sentiment.event_extractor import (
    SentimentEventExtractor,
)
from trading.research.sentiment.freshness import freshness_weight
from trading.research.sentiment.market_breadth import (
    MarketBreadthService,
)
from trading.research.sentiment.models import EventBundle
from trading.research.sentiment.policy import (
    SENTIMENT_ALGORITHM_VERSION,
    SENTIMENT_EXTRACTOR_VERSION,
    SENTIMENT_PROMPT_VERSION,
    policy_for,
)
from trading.research.sentiment.repository import SentimentRepository
from trading.research.sentiment.schemas import (
    MarketBreadthSnapshot,
    SentimentAnalyzeRequest,
    SentimentAnalyzeResponse,
    SentimentEventAnalysis,
    SentimentRiskFlag,
    SentimentSymbolSnapshot,
    SentimentVerificationStatus,
)
from trading.research.sentiment.scorer import calculate_event_score
from trading.research.sentiment.source_quality import source_quality
from trading.research.sentiment.verification import verification_result
from trading.schemas import AnalysisMode


def _stable_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _unique_flags(
    flags: list[SentimentRiskFlag],
) -> list[SentimentRiskFlag]:
    return sorted(set(flags), key=lambda flag: flag.value)


class SentimentAnalysisService:
    """Auditable sentiment engine with model inference/local scoring separation."""

    def __init__(
        self,
        repository: SentimentRepository | None = None,
        factor_repository: FactorOutputRepository | None = None,
        extractor: SentimentEventExtractor | None = None,
        market_breadth_service: MarketBreadthService | None = None,
        now_factory: type[datetime] | None = None,
    ) -> None:
        self.repository = repository or SentimentRepository()
        self.factor_repository = (
            factor_repository or FactorOutputRepository()
        )
        self.extractor = extractor or SentimentEventExtractor()
        self.market_breadth_service = (
            market_breadth_service
            or MarketBreadthService(self.repository)
        )
        self.now_factory = now_factory or datetime

    def _now(self) -> datetime:
        return self.now_factory.now().astimezone()

    @staticmethod
    def _relevance(
        bundle: EventBundle,
        extracted_symbols: list[str],
    ) -> dict[str, float]:
        relevance = {symbol: 1.0 for symbol in bundle.symbols}
        for symbol in extracted_symbols:
            if symbol and symbol not in relevance:
                relevance[symbol] = 0.5
        return relevance

    async def analyze_event(
        self,
        bundle: EventBundle,
        *,
        data_cutoff: datetime,
        allow_model_calls: bool,
        persist: bool,
        extra_risk_flags: list[SentimentRiskFlag] | None = None,
    ) -> SentimentEventAnalysis:
        if bundle.event_time > data_cutoff:
            raise ValueError("future event cannot enter sentiment analysis")
        if data_cutoff > self._now():
            raise ValueError("data_cutoff must not be in the future")

        extraction_result = await self.extractor.extract(
            bundle,
            allow_model_calls=allow_model_calls,
        )
        extraction = extraction_result.extraction
        quality = source_quality(bundle, event_type=extraction.event_type)
        verification = verification_result(bundle)
        flags = [
            *extraction_result.risk_flags,
            *quality.risk_flags,
            *verification.risk_flags,
            *(extra_risk_flags or []),
        ]
        if SentimentRiskFlag.EVENT_CONFLICT in flags:
            verification_status = SentimentVerificationStatus.CONFLICT
            verification_weight = 0.0
        else:
            verification_status = verification.status
            verification_weight = verification.weight
        freshness = freshness_weight(
            event_time=bundle.event_time,
            data_cutoff=data_cutoff,
            impact_horizon=extraction.impact_horizon,
        )
        flags.extend(freshness.risk_flags)
        relevance = self._relevance(
            bundle,
            extraction.affected_symbols,
        )
        if any(value < 0.75 for value in relevance.values()):
            flags.append(SentimentRiskFlag.SYMBOL_RELEVANCE_LOW)
        relevance_weight = max(relevance.values(), default=0.0)
        score = calculate_event_score(
            direction=extraction.direction,
            intensity=extraction.intensity,
            model_confidence=extraction.confidence,
            source_quality_weight=quality.weight,
            freshness_weight=freshness.weight,
            verification_weight=verification_weight,
            relevance_weight=relevance_weight,
            verification_status=verification_status,
        )
        if score.missing_components:
            flags.append(SentimentRiskFlag.DATA_GAP)
        evidence_ids = [
            bundle.event_cluster_id,
            *(source.record_id for source in bundle.source_records),
        ]
        input_payload = {
            "event_cluster_id": bundle.event_cluster_id,
            "cluster_hash": bundle.cluster_hash,
            "data_cutoff": data_cutoff.isoformat(),
            "extraction": extraction.model_dump(mode="json"),
            "verification": verification_status.value,
            "quality": quality.weight,
            "freshness": freshness.weight,
            "relevance": relevance,
            "algorithm_version": SENTIMENT_ALGORITHM_VERSION,
        }
        input_hash = _stable_hash(input_payload)
        generated_at = self._now()
        analysis = SentimentEventAnalysis(
            sentiment_analysis_id="sea_" + input_hash[:32],
            event_cluster_id=bundle.event_cluster_id,
            event_type=extraction.event_type,
            direction=extraction.direction,
            intensity=extraction.intensity,
            confidence=score.confidence,
            model_confidence=extraction.confidence,
            impact_horizon=extraction.impact_horizon,
            fact_type=extraction.fact_type,
            affected_symbols=sorted(relevance),
            affected_sectors=sorted(set(extraction.affected_sectors)),
            relevance_by_symbol=relevance,
            summary=extraction.summary,
            source_level=quality.source_level,
            source_quality=quality.weight,
            freshness_weight=freshness.weight,
            verification_status=verification_status,
            verification_weight=verification_weight,
            relevance_weight=relevance_weight,
            event_score=score.score,
            propagation_heat=math.log1p(max(0, bundle.source_count - 1)),
            risk_flags=_unique_flags(flags),
            evidence_ids=list(dict.fromkeys(evidence_ids)),
            model_call_ids=list(
                dict.fromkeys(extraction_result.model_call_ids)
            ),
            extractor_version=SENTIMENT_EXTRACTOR_VERSION,
            algorithm_version=SENTIMENT_ALGORITHM_VERSION,
            prompt_version=SENTIMENT_PROMPT_VERSION,
            input_snapshot_hash=input_hash,
            generated_at=generated_at,
            data_cutoff=data_cutoff,
        )
        return (
            self.repository.save_analysis(analysis)
            if persist
            else analysis
        )

    def _factor(
        self,
        *,
        snapshot: SentimentSymbolSnapshot,
        market: MarketBreadthSnapshot | None,
    ) -> FactorOutput | None:
        evidence_ids = [snapshot.snapshot_id]
        if market is not None and market.evidence_ids:
            evidence_ids.append(market.market_snapshot_id)
        if not evidence_ids:
            return None
        market_score = market.score if market is not None else 0.0
        market_confidence = market.confidence if market is not None else 0.0
        score = max(
            -1.0,
            min(
                1.0,
                snapshot.weighted_event_score * 0.8 + market_score * 0.2,
            ),
        )
        confidence = (
            snapshot.confidence * 0.8 + market_confidence * 0.2
        )
        risk_flags = {
            flag.value for flag in snapshot.risk_flags
        }
        if market is not None:
            risk_flags.update(flag.value for flag in market.risk_flags)
        factor_hash = _stable_hash(
            {
                "snapshot": snapshot.input_snapshot_hash,
                "market": (
                    market.input_snapshot_hash if market is not None else None
                ),
                "formula": "symbol_0.8_market_0.2_shadow_only",
            }
        )
        factor = FactorOutput(
            factor_id="sfo_" + factor_hash[:32],
            symbol=snapshot.symbol,
            factor_type=FactorType.SENTIMENT,
            score=score,
            confidence=max(0.0, min(1.0, confidence)),
            data_cutoff=snapshot.data_cutoff,
            generated_at=max(
                snapshot.generated_at,
                market.generated_at if market is not None else snapshot.generated_at,
            ),
            evidence_ids=evidence_ids,
            risk_flags=sorted(risk_flags),
            model_call_ids=snapshot.model_call_ids,
            algorithm_version=SENTIMENT_ALGORITHM_VERSION,
            input_snapshot_hash=factor_hash,
            shadow_mode=True,
            metadata={
                "event_count": snapshot.event_count,
                "positive_event_count": snapshot.positive_event_count,
                "negative_event_count": snapshot.negative_event_count,
                "propagation_heat": snapshot.propagation_heat,
                "market_breadth_score": market_score,
                "top_event_ids": [
                    *snapshot.top_positive_events,
                    *snapshot.top_negative_events,
                ][:10],
                "missing_fields": snapshot.missing_fields,
                "analysis_mode": snapshot.analysis_mode.value,
                "formal_strategy_weight": 0.0,
                "no_hidden_chain_of_thought": True,
            },
        )
        return self.factor_repository.save(factor)

    async def analyze(
        self,
        request: SentimentAnalyzeRequest,
    ) -> SentimentAnalyzeResponse:
        now = self._now()
        if request.data_cutoff > now:
            raise ValueError("data_cutoff must not be in the future")
        policy = policy_for(request.analysis_mode)
        allow_models = (
            request.allow_model_calls and policy.allow_model_calls
        )

        if request.analysis_mode == AnalysisMode.SCREENING:
            snapshot = (
                self.repository.latest_symbol_snapshot(
                    symbol=request.symbol,
                    data_cutoff=request.data_cutoff,
                )
                if request.symbol is not None
                else None
            )
            market = self.repository.latest_market_snapshot(
                data_cutoff=request.data_cutoff,
            )
            if market is None and request.include_market_breadth:
                market = self.market_breadth_service.calculate(
                    analysis_mode=AnalysisMode.SCREENING,
                    data_cutoff=request.data_cutoff,
                    persist=False,
                )
            missing = []
            flags = [SentimentRiskFlag.MODE_RESTRICTION]
            if request.symbol is not None and snapshot is None:
                missing.append("sentiment_symbol_snapshot")
                flags.append(SentimentRiskFlag.DATA_GAP)
            evidence = []
            if snapshot is not None:
                evidence.extend(snapshot.evidence_ids)
            if market is not None:
                evidence.extend(market.evidence_ids)
            return SentimentAnalyzeResponse(
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=request.data_cutoff,
                analyses=[],
                symbol_snapshot=snapshot,
                market_snapshot=market,
                factor_output=None,
                missing_fields=missing,
                risk_flags=_unique_flags(flags),
                evidence_ids=list(dict.fromkeys(evidence)),
            )

        bundles = self.repository.list_event_bundles(
            data_cutoff=request.data_cutoff,
            symbol=request.symbol,
            event_cluster_ids=request.event_cluster_ids,
            limit=settings.sentiment_max_events_per_request,
            strict_point_in_time=policy.strict_point_in_time,
        )
        analyses = [
            await self.analyze_event(
                bundle,
                data_cutoff=request.data_cutoff,
                allow_model_calls=allow_models,
                persist=policy.persist_analysis,
            )
            for bundle in bundles
        ]
        snapshot = None
        if request.symbol is not None:
            snapshot = aggregate_symbol(
                symbol=request.symbol,
                analyses=analyses,
                analysis_mode=request.analysis_mode,
                data_cutoff=request.data_cutoff,
                generated_at=self._now(),
            )
            if policy.persist_analysis:
                snapshot = self.repository.save_symbol_snapshot(snapshot)
        market = None
        if request.include_market_breadth:
            market = self.market_breadth_service.calculate(
                analysis_mode=request.analysis_mode,
                data_cutoff=request.data_cutoff,
                persist=policy.persist_analysis,
            )
        factor = (
            self._factor(snapshot=snapshot, market=market)
            if snapshot is not None and policy.persist_factor
            else None
        )
        missing = []
        flags: list[SentimentRiskFlag] = []
        evidence: list[str] = []
        if snapshot is None and request.symbol is not None:
            missing.append("sentiment_symbol_snapshot")
            flags.append(SentimentRiskFlag.DATA_GAP)
        if snapshot is not None:
            missing.extend(snapshot.missing_fields)
            flags.extend(snapshot.risk_flags)
            evidence.extend(snapshot.evidence_ids)
        if market is not None:
            missing.extend(market.missing_fields)
            flags.extend(market.risk_flags)
            evidence.extend(market.evidence_ids)
        return SentimentAnalyzeResponse(
            analysis_mode=request.analysis_mode,
            data_cutoff=request.data_cutoff,
            analyses=analyses,
            symbol_snapshot=snapshot,
            market_snapshot=market,
            factor_output=factor,
            missing_fields=sorted(set(missing)),
            risk_flags=_unique_flags(flags),
            evidence_ids=list(dict.fromkeys(evidence)),
        )


class ScreeningSentimentService:
    def __init__(self, service: SentimentAnalysisService) -> None:
        self.service = service

    async def analyze(
        self,
        *,
        symbol: str | None,
        data_cutoff: datetime,
    ) -> SentimentAnalyzeResponse:
        return await self.service.analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.SCREENING,
                symbol=symbol,
                data_cutoff=data_cutoff,
                allow_model_calls=False,
            )
        )


class ResearchSentimentService:
    def __init__(self, service: SentimentAnalysisService) -> None:
        self.service = service

    async def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        allow_model_calls: bool = True,
    ) -> SentimentAnalyzeResponse:
        return await self.service.analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                symbol=symbol,
                data_cutoff=data_cutoff,
                allow_model_calls=allow_model_calls,
            )
        )


class DecisionSentimentService:
    def __init__(self, service: SentimentAnalysisService) -> None:
        self.service = service

    async def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        allow_model_calls: bool = False,
    ) -> SentimentAnalyzeResponse:
        return await self.service.analyze(
            SentimentAnalyzeRequest(
                analysis_mode=AnalysisMode.DECISION,
                symbol=symbol,
                data_cutoff=data_cutoff,
                allow_model_calls=allow_model_calls,
            )
        )


__all__ = [
    "DecisionSentimentService",
    "ResearchSentimentService",
    "ScreeningSentimentService",
    "SentimentAnalysisService",
]
