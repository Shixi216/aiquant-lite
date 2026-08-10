from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from config.settings import settings
from data_hub.repositories import FactorOutputRepository
from data_hub.schemas.unified import FactorOutput, FactorType
from trading.research.policy_news.aggregator import (
    aggregate_sector,
    aggregate_symbol,
)
from trading.research.policy_news.extractor import (
    PolicyNewsExtractor,
    assess_text_completeness,
)
from trading.research.policy_news.implementation_status import (
    classify_implementation,
)
from trading.research.policy_news.models import EventBundle
from trading.research.policy_news.policy import (
    POLICY_NEWS_EXTRACTOR_VERSION,
    POLICY_NEWS_MAPPING_VERSION,
    POLICY_NEWS_PROMPT_VERSION,
    POLICY_NEWS_SCORER_VERSION,
    policy_for,
)
from trading.research.policy_news.relevance import map_relevance
from trading.research.policy_news.repository import PolicyNewsRepository
from trading.research.policy_news.schemas import (
    PolicyEventCategory,
    PolicyFactType,
    PolicyNewsAnalyzeRequest,
    PolicyNewsAnalyzeResponse,
    PolicyNewsEventAnalysis,
    PolicyNewsSymbolSnapshot,
    PolicyRiskFlag,
    PolicyVerificationStatus,
    TextCompleteness,
)
from trading.research.policy_news.scorer import (
    calculate_policy_news_score,
    freshness_weight,
)
from trading.research.policy_news.source_ranker import rank_source
from trading.schemas import AnalysisMode


SHANGHAI = ZoneInfo("Asia/Shanghai")


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


def _unique_flags(flags: list[PolicyRiskFlag]) -> list[PolicyRiskFlag]:
    return sorted(set(flags), key=lambda flag: flag.value)


def _parse_publication_value(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return (
            value.replace(tzinfo=SHANGHAI)
            if value.tzinfo is None
            else value
        )
    if isinstance(value, date):
        return datetime.combine(
            value,
            time(
                hour=settings.policy_news_date_only_available_hour,
                minute=59,
            ),
            tzinfo=SHANGHAI,
        )
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed_date = date.fromisoformat(normalized[:10])
        except ValueError:
            return None
        return datetime.combine(
            parsed_date,
            time(
                hour=settings.policy_news_date_only_available_hour,
                minute=59,
            ),
            tzinfo=SHANGHAI,
        )
    if parsed.tzinfo is None:
        if len(normalized) <= 10:
            return datetime.combine(
                parsed.date(),
                time(
                    hour=settings.policy_news_date_only_available_hour,
                    minute=59,
                ),
                tzinfo=SHANGHAI,
            )
        return parsed.replace(tzinfo=SHANGHAI)
    return parsed


def _publication_time(bundle: EventBundle) -> datetime | None:
    payload = bundle.primary_source.payload
    for key in (
        "publication_time",
        "published_at",
        "announcement_date",
        "publish_time",
        "release_time",
    ):
        parsed = _parse_publication_value(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _available_text(bundle: EventBundle) -> str:
    payload = bundle.primary_source.payload
    parts = [
        str(payload.get(key) or "").strip()
        for key in (
            "title",
            "full_text",
            "document_text",
            "content",
            "body",
            "text",
        )
    ]
    return "\n".join(part for part in parts if part)


class PolicyNewsAnalysisService:
    """Shadow-only policy/news analysis with local final scoring."""

    def __init__(
        self,
        repository: PolicyNewsRepository | None = None,
        factor_repository: FactorOutputRepository | None = None,
        extractor: PolicyNewsExtractor | None = None,
        now_factory: type[datetime] | None = None,
    ) -> None:
        self.repository = repository or PolicyNewsRepository()
        self.factor_repository = (
            factor_repository or FactorOutputRepository()
        )
        self.extractor = extractor or PolicyNewsExtractor()
        self.now_factory = now_factory or datetime

    def _now(self) -> datetime:
        return self.now_factory.now().astimezone()

    async def analyze_event(
        self,
        bundle: EventBundle,
        *,
        analysis_mode: AnalysisMode,
        data_cutoff: datetime,
        allow_model_calls: bool,
        persist: bool,
        extra_risk_flags: list[PolicyRiskFlag] | None = None,
    ) -> PolicyNewsEventAnalysis:
        if bundle.event_time > data_cutoff:
            raise ValueError("future event cannot enter policy analysis")
        if data_cutoff > self._now():
            raise ValueError("data_cutoff must not be in the future")
        publication_time = _publication_time(bundle)
        fetched_at = max(
            source.fetched_at for source in bundle.source_records
        )
        data_available_time = max(
            bundle.data_cutoff,
            fetched_at,
            *(
                [publication_time]
                if publication_time is not None
                else []
            ),
        )
        if data_available_time > data_cutoff:
            raise ValueError(
                "event was not available at the requested data_cutoff"
            )
        completeness = assess_text_completeness(bundle)
        extraction_result = await self.extractor.extract(
            bundle,
            allow_model_calls=allow_model_calls,
        )
        extraction = extraction_result.extraction
        source = rank_source(bundle)
        implementation = classify_implementation(
            text=_available_text(bundle),
            event_category=extraction.event_category,
            event_time=bundle.event_time,
            official_source=source.authority_weight >= 0.9,
        )
        relevance = map_relevance(
            bundle,
            candidate_symbols=extraction.affected_symbols,
            candidate_sectors=extraction.affected_sectors,
        )
        flags = [
            *extraction_result.risk_flags,
            *source.risk_flags,
            *implementation.risk_flags,
            *relevance.risk_flags,
            *(extra_risk_flags or []),
        ]
        if completeness != TextCompleteness.FULL_TEXT:
            flags.append(PolicyRiskFlag.TEXT_INCOMPLETE)
        if completeness in {
            TextCompleteness.TITLE_ONLY,
            TextCompleteness.TITLE_AND_METADATA,
        }:
            flags.append(PolicyRiskFlag.TITLE_ONLY_SOURCE)
        if completeness in {
            TextCompleteness.UNREADABLE,
            TextCompleteness.IMAGE_ONLY,
        }:
            flags.append(PolicyRiskFlag.DATA_GAP)
        direction = extraction.direction
        verification_status = source.verification_status
        verification_weight = source.verification_weight
        if PolicyRiskFlag.DUPLICATE_SUSPECTED in flags:
            # Keep both immutable event records for audit, but do not let a
            # not-yet-resolved repost pair multiply directional evidence.
            direction = 0
        if PolicyRiskFlag.EVENT_CONFLICT in flags:
            verification_status = PolicyVerificationStatus.CONFLICT
            verification_weight = 0.0
            direction = 0
        if publication_time is None:
            flags.append(PolicyRiskFlag.PUBLICATION_TIME_MISSING)
            if analysis_mode == AnalysisMode.DECISION:
                direction = 0
        model_confidence = extraction.confidence
        if completeness in {
            TextCompleteness.TITLE_ONLY,
            TextCompleteness.TITLE_AND_METADATA,
        }:
            model_confidence = min(
                model_confidence,
                settings.policy_news_title_only_confidence_cap,
            )
        elif completeness == TextCompleteness.PARTIAL_TEXT:
            model_confidence = min(
                model_confidence,
                settings.policy_news_partial_text_confidence_cap,
            )
        elif completeness in {
            TextCompleteness.IMAGE_ONLY,
            TextCompleteness.UNREADABLE,
        } and not extraction_result.used_model:
            model_confidence = 0.0
            direction = 0
        freshness = freshness_weight(
            event_time=bundle.event_time,
            data_cutoff=data_cutoff,
            impact_horizon=extraction.impact_horizon,
        )
        score = calculate_policy_news_score(
            direction=direction,
            intensity=extraction.intensity,
            model_confidence=model_confidence,
            source_authority_weight=source.authority_weight,
            freshness=freshness,
            implementation_weight=implementation.weight,
            verification_weight=verification_weight,
            relevance_weight=(
                relevance.max_weight
                if relevance.max_weight > 0
                else None
            ),
            verification_status=verification_status,
            implementation_status=implementation.status,
        )
        if score.missing_components:
            flags.append(PolicyRiskFlag.DATA_GAP)
        shared_sentiment_ids = (
            self.repository.shared_sentiment_analysis_ids(
                bundle.event_cluster_id,
                data_cutoff=data_cutoff,
            )
        )
        if shared_sentiment_ids:
            flags.append(PolicyRiskFlag.SHARED_SENTIMENT_EVENT)
        evidence_ids = list(
            dict.fromkeys(
                [
                    bundle.event_cluster_id,
                    *(source.record_id for source in bundle.source_records),
                ]
            )
        )
        input_payload = {
            "event_cluster_id": bundle.event_cluster_id,
            "cluster_hash": bundle.cluster_hash,
            "data_cutoff": data_cutoff.isoformat(),
            "publication_time": (
                publication_time.isoformat()
                if publication_time is not None
                else None
            ),
            "data_available_time": data_available_time.isoformat(),
            "extraction": extraction.model_dump(mode="json"),
            "source": {
                "level": source.source_level,
                "authority": source.authority_weight,
                "verification": verification_status.value,
            },
            "implementation": {
                "status": implementation.status.value,
                "weight": implementation.weight,
            },
            "relevance": {
                "symbols": [
                    item.model_dump(mode="json")
                    for item in relevance.symbols
                ],
                "sectors": [
                    item.model_dump(mode="json")
                    for item in relevance.sectors
                ],
            },
            "scorer_version": POLICY_NEWS_SCORER_VERSION,
        }
        input_hash = _stable_hash(input_payload)
        generated_at = self._now()
        fact_type = (
            extraction.fact_type
            if extraction.fact_type == PolicyFactType.MODEL_INFERENCE
            else source.fact_type
        )
        analysis = PolicyNewsEventAnalysis(
            policy_analysis_id="pna_" + input_hash[:32],
            event_cluster_id=bundle.event_cluster_id,
            event_category=extraction.event_category,
            event_type=extraction.event_type,
            direction=direction,
            intensity=extraction.intensity,
            confidence=min(score.confidence, model_confidence),
            model_confidence=model_confidence,
            fact_type=fact_type,
            source_level=source.source_level,
            source_authority=source.authority_weight,
            implementation_status=implementation.status,
            implementation_confidence=implementation.confidence,
            implementation_weight=implementation.weight,
            impact_horizon=extraction.impact_horizon,
            text_completeness=completeness,
            affected_sectors=[
                item.sector for item in relevance.sectors
            ],
            affected_symbols=[
                item.symbol for item in relevance.symbols
            ],
            symbol_relevance=list(relevance.symbols),
            sector_relevance=list(relevance.sectors),
            summary=extraction.summary,
            key_facts=extraction.key_facts,
            amounts=extraction.amounts,
            dates=extraction.dates,
            entities=extraction.entities,
            conditions=extraction.conditions,
            shared_sentiment_analysis_ids=shared_sentiment_ids,
            evidence_ids=evidence_ids,
            model_call_ids=extraction_result.model_call_ids,
            risk_flags=_unique_flags(flags),
            verification_status=verification_status,
            verification_weight=verification_weight,
            freshness_weight=freshness or 0.0,
            relevance_weight=relevance.max_weight,
            policy_news_score=score.score,
            extractor_version=POLICY_NEWS_EXTRACTOR_VERSION,
            scorer_version=POLICY_NEWS_SCORER_VERSION,
            prompt_version=POLICY_NEWS_PROMPT_VERSION,
            mapping_version=POLICY_NEWS_MAPPING_VERSION,
            input_snapshot_hash=input_hash,
            event_time=bundle.event_time,
            publication_time=publication_time,
            data_available_time=data_available_time,
            fetched_at=fetched_at,
            implementation_time=implementation.implementation_time,
            termination_time=implementation.termination_time,
            generated_at=generated_at,
            data_cutoff=data_cutoff,
        )
        should_persist = (
            persist
            and analysis.event_category
            not in {
                PolicyEventCategory.OTHER,
                PolicyEventCategory.NOT_APPLICABLE,
            }
        )
        return (
            self.repository.save_analysis(analysis)
            if should_persist
            else analysis
        )

    def _factor(
        self,
        snapshot: PolicyNewsSymbolSnapshot,
    ) -> FactorOutput:
        factor_hash = _stable_hash(
            {
                "snapshot": snapshot.input_snapshot_hash,
                "formula": "policy_symbol_shadow_only",
                "formal_strategy_weight": 0.0,
            }
        )
        factor = FactorOutput(
            factor_id="pfo_" + factor_hash[:32],
            symbol=snapshot.symbol,
            factor_type=FactorType.POLICY_NEWS,
            score=snapshot.weighted_policy_score,
            confidence=snapshot.confidence,
            data_cutoff=snapshot.data_cutoff,
            generated_at=snapshot.generated_at,
            evidence_ids=[snapshot.snapshot_id],
            risk_flags=[
                flag.value for flag in snapshot.risk_flags
            ],
            model_call_ids=snapshot.model_call_ids,
            algorithm_version=POLICY_NEWS_SCORER_VERSION,
            input_snapshot_hash=factor_hash,
            shadow_mode=True,
            metadata={
                "event_count": snapshot.event_count,
                "high_authority_event_count": (
                    snapshot.high_authority_event_count
                ),
                "implemented_event_count": (
                    snapshot.implemented_event_count
                ),
                "conflict_event_count": snapshot.conflict_event_count,
                "shared_sentiment_event_ids": (
                    snapshot.shared_sentiment_event_ids
                ),
                "factor_correlation_audit_required": bool(
                    snapshot.shared_sentiment_event_ids
                ),
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
        request: PolicyNewsAnalyzeRequest,
    ) -> PolicyNewsAnalyzeResponse:
        now = self._now()
        if request.data_cutoff > now:
            raise ValueError("data_cutoff must not be in the future")
        mode_policy = policy_for(request.analysis_mode)
        if request.analysis_mode == AnalysisMode.SCREENING:
            symbol_snapshot = (
                self.repository.latest_symbol_snapshot(
                    symbol=request.symbol,
                    data_cutoff=request.data_cutoff,
                )
                if request.symbol is not None
                else None
            )
            sector_snapshot = (
                self.repository.latest_sector_snapshot(
                    sector=request.sector,
                    data_cutoff=request.data_cutoff,
                )
                if request.sector is not None
                else None
            )
            missing = []
            flags = [PolicyRiskFlag.MODE_RESTRICTION]
            if request.symbol is not None and symbol_snapshot is None:
                missing.append("policy_news_symbol_snapshot")
                flags.append(PolicyRiskFlag.DATA_GAP)
            if request.sector is not None and sector_snapshot is None:
                missing.append("policy_news_sector_snapshot")
                flags.extend(
                    [
                        PolicyRiskFlag.DATA_GAP,
                        PolicyRiskFlag.SECTOR_MAPPING_MISSING,
                    ]
                )
            snapshot = symbol_snapshot or sector_snapshot
            return PolicyNewsAnalyzeResponse(
                analysis_mode=AnalysisMode.SCREENING,
                data_cutoff=request.data_cutoff,
                analyses=[],
                symbol_snapshot=symbol_snapshot,
                sector_snapshot=sector_snapshot,
                factor_output=None,
                missing_fields=missing,
                risk_flags=_unique_flags(flags),
                evidence_ids=(
                    snapshot.evidence_ids if snapshot is not None else []
                ),
                shared_sentiment_event_ids=(
                    snapshot.shared_sentiment_event_ids
                    if snapshot is not None
                    else []
                ),
            )

        allow_models = (
            request.allow_model_calls and mode_policy.allow_model_calls
        )
        bundles = self.repository.list_event_bundles(
            data_cutoff=request.data_cutoff,
            symbol=request.symbol,
            event_cluster_ids=request.event_cluster_ids,
            limit=None,
            strict_point_in_time=mode_policy.strict_point_in_time,
        )
        bundles = sorted(
            bundles,
            key=lambda item: (
                item.event_time,
                item.event_cluster_id,
            ),
            reverse=True,
        )[: settings.policy_news_max_events_per_request]
        if request.sector is not None:
            bundles = [
                bundle
                for bundle in bundles
                if request.sector in bundle.sectors
            ]
        analyses: list[PolicyNewsEventAnalysis] = []
        for bundle in bundles:
            try:
                analyses.append(
                    await self.analyze_event(
                        bundle,
                        analysis_mode=request.analysis_mode,
                        data_cutoff=request.data_cutoff,
                        allow_model_calls=allow_models,
                        persist=mode_policy.persist_analysis,
                    )
                )
            except ValueError as exc:
                if "not available" not in str(exc):
                    raise
        symbol_snapshot = None
        if request.symbol is not None:
            symbol_snapshot = aggregate_symbol(
                symbol=request.symbol,
                analyses=analyses,
                analysis_mode=request.analysis_mode,
                data_cutoff=request.data_cutoff,
                generated_at=self._now(),
            )
            if mode_policy.persist_analysis:
                symbol_snapshot = self.repository.save_symbol_snapshot(
                    symbol_snapshot
                )
        sector_snapshot = None
        if request.sector is not None:
            sector_snapshot = aggregate_sector(
                sector=request.sector,
                analyses=analyses,
                analysis_mode=request.analysis_mode,
                data_cutoff=request.data_cutoff,
                generated_at=self._now(),
            )
            if mode_policy.persist_analysis:
                sector_snapshot = self.repository.save_sector_snapshot(
                    sector_snapshot
                )
        factor = (
            self._factor(symbol_snapshot)
            if symbol_snapshot is not None and mode_policy.persist_factor
            else None
        )
        snapshot = symbol_snapshot or sector_snapshot
        risk_flags = (
            snapshot.risk_flags if snapshot is not None else []
        )
        missing_fields = (
            snapshot.missing_fields if snapshot is not None else []
        )
        evidence_ids = (
            snapshot.evidence_ids if snapshot is not None else []
        )
        shared_events = sorted(
            {
                analysis.event_cluster_id
                for analysis in analyses
                if analysis.shared_sentiment_analysis_ids
            }
        )
        return PolicyNewsAnalyzeResponse(
            analysis_mode=request.analysis_mode,
            data_cutoff=request.data_cutoff,
            analyses=analyses,
            symbol_snapshot=symbol_snapshot,
            sector_snapshot=sector_snapshot,
            factor_output=factor,
            missing_fields=missing_fields,
            risk_flags=risk_flags,
            evidence_ids=evidence_ids,
            implementation_status=sorted(
                {item.implementation_status for item in analyses},
                key=lambda item: item.value,
            ),
            text_completeness=sorted(
                {item.text_completeness for item in analyses},
                key=lambda item: item.value,
            ),
            shared_sentiment_event_ids=shared_events,
        )


class ScreeningPolicyNewsService:
    def __init__(self, service: PolicyNewsAnalysisService) -> None:
        self.service = service

    async def analyze(
        self,
        *,
        symbol: str | None,
        data_cutoff: datetime,
    ) -> PolicyNewsAnalyzeResponse:
        return await self.service.analyze(
            PolicyNewsAnalyzeRequest(
                analysis_mode=AnalysisMode.SCREENING,
                symbol=symbol,
                data_cutoff=data_cutoff,
            )
        )


class ResearchPolicyNewsService:
    def __init__(self, service: PolicyNewsAnalysisService) -> None:
        self.service = service

    async def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        allow_model_calls: bool = True,
        allow_external_fetch: bool = True,
    ) -> PolicyNewsAnalyzeResponse:
        return await self.service.analyze(
            PolicyNewsAnalyzeRequest(
                analysis_mode=AnalysisMode.RESEARCH,
                symbol=symbol,
                data_cutoff=data_cutoff,
                allow_model_calls=allow_model_calls,
                allow_external_fetch=allow_external_fetch,
            )
        )


class DecisionPolicyNewsService:
    def __init__(self, service: PolicyNewsAnalysisService) -> None:
        self.service = service

    async def analyze(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        allow_model_calls: bool = False,
    ) -> PolicyNewsAnalyzeResponse:
        return await self.service.analyze(
            PolicyNewsAnalyzeRequest(
                analysis_mode=AnalysisMode.DECISION,
                symbol=symbol,
                data_cutoff=data_cutoff,
                allow_model_calls=allow_model_calls,
                allow_external_fetch=False,
            )
        )


__all__ = [
    "DecisionPolicyNewsService",
    "PolicyNewsAnalysisService",
    "ResearchPolicyNewsService",
    "ScreeningPolicyNewsService",
]
