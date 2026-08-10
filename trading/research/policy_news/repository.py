from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from database.db import get_connection, initialize_database
from trading.research.policy_news.models import EventBundle
from trading.research.policy_news.schemas import (
    PolicyNewsEvaluation,
    PolicyNewsEventAnalysis,
    PolicyNewsSectorSnapshot,
    PolicyNewsSymbolSnapshot,
)
from trading.research.sentiment.repository import SentimentRepository
from trading.schemas import AnalysisMode


def _json_load(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


class PolicyNewsRepository:
    """Point-in-time policy reads over the shared event layer."""

    def __init__(self) -> None:
        initialize_database()
        self._events = SentimentRepository()

    def get_event_bundle(self, event_cluster_id: str) -> EventBundle | None:
        return self._events.get_event_bundle(event_cluster_id)

    def list_event_bundles(
        self,
        *,
        data_cutoff: datetime,
        symbol: str | None = None,
        event_cluster_ids: list[str] | None = None,
        start_time: datetime | None = None,
        event_type: str | None = None,
        limit: int | None = None,
        strict_point_in_time: bool = True,
    ) -> list[EventBundle]:
        return self._events.list_event_bundles(
            data_cutoff=data_cutoff,
            symbol=symbol,
            event_cluster_ids=event_cluster_ids,
            start_time=start_time,
            event_type=event_type,
            limit=limit,
            strict_point_in_time=strict_point_in_time,
        )

    @staticmethod
    def shared_sentiment_analysis_ids(
        event_cluster_id: str,
        *,
        data_cutoff: datetime,
    ) -> list[str]:
        with get_connection() as connection:
            return [
                row[0]
                for row in connection.execute(
                    """
                    SELECT sentiment_analysis_id
                    FROM sentiment_event_analyses
                    WHERE event_cluster_id = ?
                      AND data_cutoff <= ?
                    ORDER BY data_cutoff, generated_at
                    """,
                    [event_cluster_id, data_cutoff],
                ).fetchall()
            ]

    @staticmethod
    def _analysis_select() -> str:
        return """
            SELECT policy_analysis_id, event_cluster_id, event_category,
                   event_type, direction, intensity, model_confidence,
                   confidence, fact_type, source_level,
                   source_authority_weight, freshness_weight,
                   implementation_status, implementation_confidence,
                   implementation_weight, verification_status,
                   verification_weight, relevance_weight,
                   policy_news_score, impact_horizon, text_completeness,
                   affected_symbols_json, affected_sectors_json,
                   symbol_relevance_json, sector_relevance_json,
                   summary, key_facts_json, amounts_json, dates_json,
                   entities_json, conditions_json,
                   shared_sentiment_analysis_ids_json, evidence_ids_json,
                   model_call_ids_json, risk_flags_json,
                   extractor_version, scorer_version, prompt_version,
                   mapping_version, input_snapshot_hash, event_time,
                   publication_time, data_available_time, fetched_at,
                   implementation_time, termination_time, data_cutoff,
                   generated_at, shadow_mode
            FROM policy_news_event_analyses
        """

    @staticmethod
    def _analysis_from_row(row: tuple[Any, ...]) -> PolicyNewsEventAnalysis:
        return PolicyNewsEventAnalysis(
            policy_analysis_id=row[0],
            event_cluster_id=row[1],
            event_category=row[2],
            event_type=row[3],
            direction=row[4],
            intensity=row[5],
            model_confidence=row[6],
            confidence=row[7],
            fact_type=row[8],
            source_level=row[9],
            source_authority=row[10],
            freshness_weight=row[11],
            implementation_status=row[12],
            implementation_confidence=row[13],
            implementation_weight=row[14],
            verification_status=row[15],
            verification_weight=row[16],
            relevance_weight=row[17],
            policy_news_score=row[18],
            impact_horizon=row[19],
            text_completeness=row[20],
            affected_symbols=_json_load(row[21]),
            affected_sectors=_json_load(row[22]),
            symbol_relevance=_json_load(row[23]),
            sector_relevance=_json_load(row[24]),
            summary=row[25],
            key_facts=_json_load(row[26]),
            amounts=_json_load(row[27]),
            dates=_json_load(row[28]),
            entities=_json_load(row[29]),
            conditions=_json_load(row[30]),
            shared_sentiment_analysis_ids=_json_load(row[31]),
            evidence_ids=_json_load(row[32]),
            model_call_ids=_json_load(row[33]),
            risk_flags=_json_load(row[34]),
            extractor_version=row[35],
            scorer_version=row[36],
            prompt_version=row[37],
            mapping_version=row[38],
            input_snapshot_hash=row[39],
            event_time=row[40],
            publication_time=row[41],
            data_available_time=row[42],
            fetched_at=row[43],
            implementation_time=row[44],
            termination_time=row[45],
            data_cutoff=row[46],
            generated_at=row[47],
            shadow_mode=row[48],
        )

    def get_analysis(
        self,
        policy_analysis_id: str,
    ) -> PolicyNewsEventAnalysis | None:
        with get_connection() as connection:
            row = connection.execute(
                self._analysis_select()
                + " WHERE policy_analysis_id = ?",
                [policy_analysis_id],
            ).fetchone()
        return None if row is None else self._analysis_from_row(row)

    def latest_event_analysis(
        self,
        *,
        event_cluster_id: str,
        data_cutoff: datetime,
    ) -> PolicyNewsEventAnalysis | None:
        with get_connection() as connection:
            row = connection.execute(
                self._analysis_select()
                + """
                  WHERE event_cluster_id = ?
                    AND data_cutoff <= ?
                    AND data_available_time <= ?
                  ORDER BY data_cutoff DESC, generated_at DESC
                  LIMIT 1
                """,
                [event_cluster_id, data_cutoff, data_cutoff],
            ).fetchone()
        return None if row is None else self._analysis_from_row(row)

    def save_analysis(
        self,
        analysis: PolicyNewsEventAnalysis,
    ) -> PolicyNewsEventAnalysis:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO policy_news_event_analyses (
                    policy_analysis_id, event_cluster_id, event_category,
                    event_type, direction, intensity, model_confidence,
                    confidence, fact_type, source_level,
                    source_authority_weight, freshness_weight,
                    implementation_status, implementation_confidence,
                    implementation_weight, verification_status,
                    verification_weight, relevance_weight,
                    policy_news_score, impact_horizon, text_completeness,
                    affected_symbols_json, affected_sectors_json,
                    symbol_relevance_json, sector_relevance_json,
                    summary, key_facts_json, amounts_json, dates_json,
                    entities_json, conditions_json,
                    shared_sentiment_analysis_ids_json, evidence_ids_json,
                    model_call_ids_json, risk_flags_json,
                    extractor_version, scorer_version, prompt_version,
                    mapping_version, input_snapshot_hash, event_time,
                    publication_time, data_available_time, fetched_at,
                    implementation_time, termination_time, data_cutoff,
                    generated_at, shadow_mode
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    analysis.policy_analysis_id,
                    analysis.event_cluster_id,
                    analysis.event_category.value,
                    analysis.event_type.value,
                    analysis.direction,
                    analysis.intensity,
                    analysis.model_confidence,
                    analysis.confidence,
                    analysis.fact_type.value,
                    analysis.source_level,
                    analysis.source_authority,
                    analysis.freshness_weight,
                    analysis.implementation_status.value,
                    analysis.implementation_confidence,
                    analysis.implementation_weight,
                    analysis.verification_status.value,
                    analysis.verification_weight,
                    analysis.relevance_weight,
                    analysis.policy_news_score,
                    analysis.impact_horizon.value,
                    analysis.text_completeness.value,
                    _json_dump(analysis.affected_symbols),
                    _json_dump(analysis.affected_sectors),
                    _json_dump(
                        [
                            item.model_dump(mode="json")
                            for item in analysis.symbol_relevance
                        ]
                    ),
                    _json_dump(
                        [
                            item.model_dump(mode="json")
                            for item in analysis.sector_relevance
                        ]
                    ),
                    analysis.summary,
                    _json_dump(analysis.key_facts),
                    _json_dump(analysis.amounts),
                    _json_dump(analysis.dates),
                    _json_dump(analysis.entities),
                    _json_dump(analysis.conditions),
                    _json_dump(analysis.shared_sentiment_analysis_ids),
                    _json_dump(analysis.evidence_ids),
                    _json_dump(analysis.model_call_ids),
                    _json_dump(
                        [flag.value for flag in analysis.risk_flags]
                    ),
                    analysis.extractor_version,
                    analysis.scorer_version,
                    analysis.prompt_version,
                    analysis.mapping_version,
                    analysis.input_snapshot_hash,
                    analysis.event_time,
                    analysis.publication_time,
                    analysis.data_available_time,
                    analysis.fetched_at,
                    analysis.implementation_time,
                    analysis.termination_time,
                    analysis.data_cutoff,
                    analysis.generated_at,
                    True,
                ],
            )
            row = connection.execute(
                self._analysis_select()
                + """
                  WHERE event_cluster_id = ?
                    AND data_cutoff = ?
                    AND scorer_version = ?
                    AND input_snapshot_hash = ?
                  LIMIT 1
                """,
                [
                    analysis.event_cluster_id,
                    analysis.data_cutoff,
                    analysis.scorer_version,
                    analysis.input_snapshot_hash,
                ],
            ).fetchone()
        if row is None:
            raise RuntimeError("policy event analysis was not persisted")
        return self._analysis_from_row(row)

    def list_analyses(
        self,
        *,
        data_cutoff: datetime,
        symbol: str | None = None,
        sector: str | None = None,
    ) -> list[PolicyNewsEventAnalysis]:
        with get_connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT policy_analysis_id
                    FROM policy_news_event_analyses
                    WHERE data_cutoff <= ?
                      AND data_available_time <= ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY event_cluster_id
                        ORDER BY data_cutoff DESC, generated_at DESC
                    ) = 1
                    ORDER BY event_cluster_id
                    """,
                    [data_cutoff, data_cutoff],
                ).fetchall()
            ]
        analyses = [
            analysis
            for item_id in ids
            if (analysis := self.get_analysis(item_id)) is not None
        ]
        if symbol is not None:
            analyses = [
                item for item in analyses if symbol in item.affected_symbols
            ]
        if sector is not None:
            analyses = [
                item for item in analyses if sector in item.affected_sectors
            ]
        return analyses

    @staticmethod
    def _snapshot_select(table: str, target_column: str) -> str:
        return f"""
            SELECT snapshot_id, {target_column}, analysis_mode, data_cutoff,
                   event_count, positive_event_count, negative_event_count,
                   neutral_event_count, weighted_policy_score,
                   high_authority_event_count, implemented_event_count,
                   conflict_event_count, confidence, horizon_scores_json,
                   top_positive_event_ids_json,
                   top_negative_event_ids_json, evidence_ids_json,
                   model_call_ids_json, shared_sentiment_event_ids_json,
                   missing_fields_json, risk_flags_json,
                   input_snapshot_hash, algorithm_version, generated_at,
                   shadow_mode
            FROM {table}
        """

    @staticmethod
    def _snapshot_payload(row: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "snapshot_id": row[0],
            "analysis_mode": row[2],
            "data_cutoff": row[3],
            "event_count": row[4],
            "positive_event_count": row[5],
            "negative_event_count": row[6],
            "neutral_event_count": row[7],
            "weighted_policy_score": row[8],
            "high_authority_event_count": row[9],
            "implemented_event_count": row[10],
            "conflict_event_count": row[11],
            "confidence": row[12],
            "horizon_scores": _json_load(row[13]),
            "top_positive_events": _json_load(row[14]),
            "top_negative_events": _json_load(row[15]),
            "evidence_ids": _json_load(row[16]),
            "model_call_ids": _json_load(row[17]),
            "shared_sentiment_event_ids": _json_load(row[18]),
            "missing_fields": _json_load(row[19]),
            "risk_flags": _json_load(row[20]),
            "input_snapshot_hash": row[21],
            "algorithm_version": row[22],
            "generated_at": row[23],
            "shadow_mode": row[24],
        }

    @classmethod
    def _symbol_from_row(
        cls,
        row: tuple[Any, ...],
    ) -> PolicyNewsSymbolSnapshot:
        return PolicyNewsSymbolSnapshot(
            symbol=row[1],
            **cls._snapshot_payload(row),
        )

    @classmethod
    def _sector_from_row(
        cls,
        row: tuple[Any, ...],
    ) -> PolicyNewsSectorSnapshot:
        return PolicyNewsSectorSnapshot(
            sector=row[1],
            **cls._snapshot_payload(row),
        )

    @staticmethod
    def _snapshot_values(
        snapshot: PolicyNewsSymbolSnapshot | PolicyNewsSectorSnapshot,
        target: str,
    ) -> list[Any]:
        return [
            snapshot.snapshot_id,
            target,
            snapshot.analysis_mode.value,
            snapshot.data_cutoff,
            snapshot.event_count,
            snapshot.positive_event_count,
            snapshot.negative_event_count,
            snapshot.neutral_event_count,
            snapshot.weighted_policy_score,
            snapshot.high_authority_event_count,
            snapshot.implemented_event_count,
            snapshot.conflict_event_count,
            snapshot.confidence,
            _json_dump(snapshot.horizon_scores),
            _json_dump(snapshot.top_positive_events),
            _json_dump(snapshot.top_negative_events),
            _json_dump(snapshot.evidence_ids),
            _json_dump(snapshot.model_call_ids),
            _json_dump(snapshot.shared_sentiment_event_ids),
            _json_dump(snapshot.missing_fields),
            _json_dump(
                [flag.value for flag in snapshot.risk_flags]
            ),
            snapshot.input_snapshot_hash,
            snapshot.algorithm_version,
            snapshot.generated_at,
            True,
        ]

    def save_symbol_snapshot(
        self,
        snapshot: PolicyNewsSymbolSnapshot,
    ) -> PolicyNewsSymbolSnapshot:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO policy_news_symbol_snapshots (
                    snapshot_id, symbol, analysis_mode, data_cutoff,
                    event_count, positive_event_count, negative_event_count,
                    neutral_event_count, weighted_policy_score,
                    high_authority_event_count, implemented_event_count,
                    conflict_event_count, confidence, horizon_scores_json,
                    top_positive_event_ids_json,
                    top_negative_event_ids_json, evidence_ids_json,
                    model_call_ids_json, shared_sentiment_event_ids_json,
                    missing_fields_json, risk_flags_json,
                    input_snapshot_hash, algorithm_version, generated_at,
                    shadow_mode
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                self._snapshot_values(snapshot, snapshot.symbol),
            )
        persisted = self.get_symbol_snapshot(snapshot.snapshot_id)
        if persisted is None:
            persisted = self.latest_symbol_snapshot(
                symbol=snapshot.symbol,
                data_cutoff=snapshot.data_cutoff,
                mode=snapshot.analysis_mode,
            )
        if persisted is None:
            raise RuntimeError("policy symbol snapshot was not persisted")
        return persisted

    def get_symbol_snapshot(
        self,
        snapshot_id: str,
    ) -> PolicyNewsSymbolSnapshot | None:
        with get_connection() as connection:
            row = connection.execute(
                self._snapshot_select(
                    "policy_news_symbol_snapshots",
                    "symbol",
                )
                + " WHERE snapshot_id = ?",
                [snapshot_id],
            ).fetchone()
        return None if row is None else self._symbol_from_row(row)

    def latest_symbol_snapshot(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        mode: AnalysisMode | None = None,
    ) -> PolicyNewsSymbolSnapshot | None:
        clauses = ["symbol = ?", "data_cutoff <= ?"]
        parameters: list[Any] = [symbol, data_cutoff]
        if mode is not None:
            clauses.append("analysis_mode = ?")
            parameters.append(mode.value)
        with get_connection() as connection:
            row = connection.execute(
                self._snapshot_select(
                    "policy_news_symbol_snapshots",
                    "symbol",
                )
                + f"""
                  WHERE {' AND '.join(clauses)}
                  ORDER BY data_cutoff DESC, generated_at DESC
                  LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else self._symbol_from_row(row)

    def save_sector_snapshot(
        self,
        snapshot: PolicyNewsSectorSnapshot,
    ) -> PolicyNewsSectorSnapshot:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO policy_news_sector_snapshots (
                    snapshot_id, sector, analysis_mode, data_cutoff,
                    event_count, positive_event_count, negative_event_count,
                    neutral_event_count, weighted_policy_score,
                    high_authority_event_count, implemented_event_count,
                    conflict_event_count, confidence, horizon_scores_json,
                    top_positive_event_ids_json,
                    top_negative_event_ids_json, evidence_ids_json,
                    model_call_ids_json, shared_sentiment_event_ids_json,
                    missing_fields_json, risk_flags_json,
                    input_snapshot_hash, algorithm_version, generated_at,
                    shadow_mode
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                self._snapshot_values(snapshot, snapshot.sector),
            )
        persisted = self.get_sector_snapshot(snapshot.snapshot_id)
        if persisted is None:
            persisted = self.latest_sector_snapshot(
                sector=snapshot.sector,
                data_cutoff=snapshot.data_cutoff,
                mode=snapshot.analysis_mode,
            )
        if persisted is None:
            raise RuntimeError("policy sector snapshot was not persisted")
        return persisted

    def get_sector_snapshot(
        self,
        snapshot_id: str,
    ) -> PolicyNewsSectorSnapshot | None:
        with get_connection() as connection:
            row = connection.execute(
                self._snapshot_select(
                    "policy_news_sector_snapshots",
                    "sector",
                )
                + " WHERE snapshot_id = ?",
                [snapshot_id],
            ).fetchone()
        return None if row is None else self._sector_from_row(row)

    def latest_sector_snapshot(
        self,
        *,
        sector: str,
        data_cutoff: datetime,
        mode: AnalysisMode | None = None,
    ) -> PolicyNewsSectorSnapshot | None:
        clauses = ["sector = ?", "data_cutoff <= ?"]
        parameters: list[Any] = [sector, data_cutoff]
        if mode is not None:
            clauses.append("analysis_mode = ?")
            parameters.append(mode.value)
        with get_connection() as connection:
            row = connection.execute(
                self._snapshot_select(
                    "policy_news_sector_snapshots",
                    "sector",
                )
                + f"""
                  WHERE {' AND '.join(clauses)}
                  ORDER BY data_cutoff DESC, generated_at DESC
                  LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else self._sector_from_row(row)

    def market_daily_records(
        self,
        *,
        data_cutoff: datetime,
    ) -> list[dict[str, Any]]:
        return self._events.market_daily_records(data_cutoff=data_cutoff)

    def save_evaluation(
        self,
        evaluation: PolicyNewsEvaluation,
    ) -> PolicyNewsEvaluation:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO policy_news_evaluations (
                    evaluation_id, snapshot_id, symbol, sector,
                    event_time, publication_time,
                    implementation_status_at_analysis, data_cutoff,
                    policy_news_score, confidence, return_1d, return_3d,
                    return_5d, return_20d, max_rise, max_drawdown,
                    actually_implemented, terminated_or_retracted,
                    data_complete, evidence_ids_json, missing_fields_json,
                    generated_at, algorithm_version
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    evaluation.evaluation_id,
                    evaluation.snapshot_id,
                    evaluation.symbol,
                    evaluation.sector,
                    evaluation.event_time,
                    evaluation.publication_time,
                    evaluation.implementation_status_at_analysis.value,
                    evaluation.data_cutoff,
                    evaluation.policy_news_score,
                    evaluation.confidence,
                    evaluation.return_1d,
                    evaluation.return_3d,
                    evaluation.return_5d,
                    evaluation.return_20d,
                    evaluation.max_rise,
                    evaluation.max_drawdown,
                    evaluation.actually_implemented,
                    evaluation.terminated_or_retracted,
                    evaluation.data_complete,
                    _json_dump(evaluation.evidence_ids),
                    _json_dump(evaluation.missing_fields),
                    evaluation.generated_at,
                    evaluation.algorithm_version,
                ],
            )
        return evaluation


__all__ = ["PolicyNewsRepository"]
