from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from database.db import get_connection, initialize_database
from trading.research.sentiment.models import (
    EventBundle,
    EventSourceRecord,
)
from trading.research.sentiment.schemas import (
    MarketBreadthSnapshot,
    SentimentEvaluation,
    SentimentEventAnalysis,
    SentimentSymbolSnapshot,
)
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


class SentimentRepository:
    """Point-in-time sentiment reads and idempotent audit writes."""

    def __init__(self) -> None:
        initialize_database()

    def get_event_bundle(
        self,
        event_cluster_id: str,
    ) -> EventBundle | None:
        with get_connection() as connection:
            cluster = connection.execute(
                """
                SELECT event_cluster_id, canonical_title, event_type,
                       event_time, data_cutoff, primary_source_id,
                       source_count, dedup_method, dedup_version,
                       cluster_hash
                FROM event_clusters
                WHERE event_cluster_id = ?
                """,
                [event_cluster_id],
            ).fetchone()
            if cluster is None:
                return None
            source_rows = connection.execute(
                """
                SELECT d.record_id, d.event_time, d.fetched_at,
                       d.source_name, d.source_url, d.source_level,
                       d.verified, d.content_hash, d.payload_json
                FROM event_source_links l
                JOIN data_records d
                  ON d.record_id = l.source_record_id
                WHERE l.event_cluster_id = ?
                ORDER BY l.is_primary DESC, d.record_id
                """,
                [event_cluster_id],
            ).fetchall()
            symbols = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT symbol
                    FROM event_symbol_links
                    WHERE event_cluster_id = ?
                    ORDER BY symbol
                    """,
                    [event_cluster_id],
                ).fetchall()
            ]
            sectors = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT sector
                    FROM event_sector_links
                    WHERE event_cluster_id = ?
                    ORDER BY sector
                    """,
                    [event_cluster_id],
                ).fetchall()
            ]
        sources = [
            EventSourceRecord(
                record_id=row[0],
                event_time=row[1],
                fetched_at=row[2],
                source_name=row[3],
                source_url=row[4],
                source_level=row[5],
                verified=row[6],
                content_hash=row[7],
                payload=_json_load(row[8]),
            )
            for row in source_rows
        ]
        return EventBundle(
            event_cluster_id=cluster[0],
            canonical_title=cluster[1],
            cluster_event_type=cluster[2],
            event_time=cluster[3],
            data_cutoff=cluster[4],
            primary_source_id=cluster[5],
            source_count=cluster[6],
            source_records=sources,
            symbols=symbols,
            sectors=sectors,
            dedup_method=cluster[7],
            dedup_version=cluster[8],
            cluster_hash=cluster[9],
        )

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
        clauses = ["c.event_time <= ?"]
        parameters: list[Any] = [data_cutoff]
        joins = ""
        if strict_point_in_time:
            clauses.append("c.data_cutoff <= ?")
            parameters.append(data_cutoff)
        if symbol is not None:
            joins += (
                " JOIN event_symbol_links sl"
                " ON sl.event_cluster_id = c.event_cluster_id"
            )
            clauses.append("sl.symbol = ?")
            parameters.append(symbol)
        if event_cluster_ids:
            placeholders = ",".join("?" for _ in event_cluster_ids)
            clauses.append(f"c.event_cluster_id IN ({placeholders})")
            parameters.extend(event_cluster_ids)
        if start_time is not None:
            clauses.append("c.event_time >= ?")
            parameters.append(start_time)
        if event_type is not None:
            clauses.append("c.event_type = ?")
            parameters.append(event_type)
        limit_sql = ""
        if limit is not None:
            limit_sql = " LIMIT ?"
            parameters.append(limit)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT DISTINCT c.event_cluster_id, c.event_time
                FROM event_clusters c
                {joins}
                WHERE {' AND '.join(clauses)}
                ORDER BY c.event_time, c.event_cluster_id
                {limit_sql}
                """,
                parameters,
            ).fetchall()
        return [
            bundle
            for row in rows
            if (bundle := self.get_event_bundle(row[0])) is not None
        ]

    @staticmethod
    def _analysis_from_row(row: tuple[Any, ...]) -> SentimentEventAnalysis:
        return SentimentEventAnalysis(
            sentiment_analysis_id=row[0],
            event_cluster_id=row[1],
            event_type=row[2],
            direction=row[3],
            intensity=row[4],
            model_confidence=row[5],
            confidence=row[6],
            source_level=row[7],
            source_quality=row[8],
            freshness_weight=row[9],
            verification_status=row[10],
            verification_weight=row[11],
            relevance_weight=row[12],
            relevance_by_symbol=_json_load(row[13]),
            event_score=row[14],
            impact_horizon=row[15],
            fact_type=row[16],
            affected_symbols=_json_load(row[17]),
            affected_sectors=_json_load(row[18]),
            summary=row[19],
            risk_flags=_json_load(row[20]),
            evidence_ids=_json_load(row[21]),
            model_call_ids=_json_load(row[22]),
            extractor_version=row[23],
            algorithm_version=row[24],
            prompt_version=row[25],
            input_snapshot_hash=row[26],
            propagation_heat=row[27],
            data_cutoff=row[28],
            generated_at=row[29],
            shadow_mode=row[30],
        )

    @staticmethod
    def _analysis_select() -> str:
        return """
            SELECT sentiment_analysis_id, event_cluster_id, event_type,
                   direction, intensity, model_confidence, confidence,
                   source_level, source_quality_weight, freshness_weight,
                   verification_status, verification_weight,
                   relevance_weight, relevance_by_symbol_json,
                   event_score, impact_horizon, fact_type,
                   affected_symbols_json, affected_sectors_json,
                   summary, risk_flags_json, evidence_ids_json,
                   model_call_ids_json, extractor_version,
                   algorithm_version, prompt_version,
                   input_snapshot_hash, propagation_heat,
                   data_cutoff, generated_at, shadow_mode
            FROM sentiment_event_analyses
        """

    def get_analysis(
        self,
        sentiment_analysis_id: str,
    ) -> SentimentEventAnalysis | None:
        with get_connection() as connection:
            row = connection.execute(
                self._analysis_select()
                + " WHERE sentiment_analysis_id = ?",
                [sentiment_analysis_id],
            ).fetchone()
        return None if row is None else self._analysis_from_row(row)

    def latest_event_analysis(
        self,
        *,
        event_cluster_id: str,
        data_cutoff: datetime,
    ) -> SentimentEventAnalysis | None:
        with get_connection() as connection:
            row = connection.execute(
                self._analysis_select()
                + """
                  WHERE event_cluster_id = ?
                    AND data_cutoff <= ?
                  ORDER BY data_cutoff DESC, generated_at DESC
                  LIMIT 1
                """,
                [event_cluster_id, data_cutoff],
            ).fetchone()
        return None if row is None else self._analysis_from_row(row)

    def save_analysis(
        self,
        analysis: SentimentEventAnalysis,
    ) -> SentimentEventAnalysis:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_event_analyses (
                    sentiment_analysis_id, event_cluster_id, event_type,
                    direction, intensity, model_confidence, confidence,
                    source_level, source_quality_weight, freshness_weight,
                    verification_status, verification_weight,
                    relevance_weight, relevance_by_symbol_json,
                    event_score, impact_horizon, fact_type,
                    affected_symbols_json, affected_sectors_json,
                    summary, risk_flags_json, evidence_ids_json,
                    model_call_ids_json, extractor_version,
                    algorithm_version, prompt_version,
                    input_snapshot_hash, propagation_heat,
                    data_cutoff, generated_at, shadow_mode
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    analysis.sentiment_analysis_id,
                    analysis.event_cluster_id,
                    analysis.event_type.value,
                    analysis.direction,
                    analysis.intensity,
                    analysis.model_confidence,
                    analysis.confidence,
                    analysis.source_level,
                    analysis.source_quality,
                    analysis.freshness_weight,
                    analysis.verification_status.value,
                    analysis.verification_weight,
                    analysis.relevance_weight,
                    _json_dump(analysis.relevance_by_symbol),
                    analysis.event_score,
                    analysis.impact_horizon.value,
                    analysis.fact_type.value,
                    _json_dump(analysis.affected_symbols),
                    _json_dump(analysis.affected_sectors),
                    analysis.summary,
                    _json_dump(
                        [flag.value for flag in analysis.risk_flags]
                    ),
                    _json_dump(analysis.evidence_ids),
                    _json_dump(analysis.model_call_ids),
                    analysis.extractor_version,
                    analysis.algorithm_version,
                    analysis.prompt_version,
                    analysis.input_snapshot_hash,
                    analysis.propagation_heat,
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
                    AND algorithm_version = ?
                    AND input_snapshot_hash = ?
                  LIMIT 1
                """,
                [
                    analysis.event_cluster_id,
                    analysis.data_cutoff,
                    analysis.algorithm_version,
                    analysis.input_snapshot_hash,
                ],
            ).fetchone()
        if row is None:
            raise RuntimeError("sentiment event analysis was not persisted")
        return self._analysis_from_row(row)

    def list_analyses(
        self,
        *,
        symbol: str | None,
        data_cutoff: datetime,
    ) -> list[SentimentEventAnalysis]:
        clauses = ["a.data_cutoff <= ?"]
        parameters: list[Any] = [data_cutoff]
        join = ""
        if symbol is not None:
            join = (
                " JOIN event_symbol_links sl"
                " ON sl.event_cluster_id = a.event_cluster_id"
            )
            clauses.append("sl.symbol = ?")
            parameters.append(symbol)
        with get_connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    f"""
                    SELECT a.sentiment_analysis_id
                    FROM sentiment_event_analyses a
                    {join}
                    WHERE {' AND '.join(clauses)}
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY a.event_cluster_id
                        ORDER BY a.data_cutoff DESC, a.generated_at DESC
                    ) = 1
                    ORDER BY a.event_cluster_id
                    """,
                    parameters,
                ).fetchall()
            ]
        return [
            item
            for analysis_id in ids
            if (item := self.get_analysis(analysis_id)) is not None
        ]

    @staticmethod
    def _symbol_from_row(row: tuple[Any, ...]) -> SentimentSymbolSnapshot:
        return SentimentSymbolSnapshot(
            snapshot_id=row[0],
            symbol=row[1],
            analysis_mode=row[2],
            data_cutoff=row[3],
            weighted_event_score=row[4],
            confidence=row[5],
            event_count=row[6],
            positive_event_count=row[7],
            negative_event_count=row[8],
            neutral_event_count=row[9],
            propagation_heat=row[10],
            top_positive_events=_json_load(row[11]),
            top_negative_events=_json_load(row[12]),
            evidence_ids=_json_load(row[13]),
            model_call_ids=_json_load(row[14]),
            missing_fields=_json_load(row[15]),
            risk_flags=_json_load(row[16]),
            input_snapshot_hash=row[17],
            shadow_mode=row[18],
            algorithm_version=row[19],
            generated_at=row[20],
        )

    @staticmethod
    def _symbol_select() -> str:
        return """
            SELECT snapshot_id, symbol, analysis_mode, data_cutoff,
                   score, confidence, event_count, positive_event_count,
                   negative_event_count, neutral_event_count,
                   propagation_heat, top_positive_event_ids_json,
                   top_negative_event_ids_json, evidence_ids_json,
                   model_call_ids_json, missing_fields_json,
                   risk_flags_json, input_snapshot_hash, shadow_mode,
                   algorithm_version, generated_at
            FROM sentiment_symbol_snapshots
        """

    def save_symbol_snapshot(
        self,
        snapshot: SentimentSymbolSnapshot,
    ) -> SentimentSymbolSnapshot:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_symbol_snapshots (
                    snapshot_id, symbol, analysis_mode, data_cutoff,
                    score, confidence, event_count,
                    positive_event_count, negative_event_count,
                    neutral_event_count, propagation_heat,
                    top_positive_event_ids_json,
                    top_negative_event_ids_json, evidence_ids_json,
                    model_call_ids_json, missing_fields_json,
                    risk_flags_json, input_snapshot_hash, shadow_mode,
                    algorithm_version, generated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    snapshot.snapshot_id,
                    snapshot.symbol,
                    snapshot.analysis_mode.value,
                    snapshot.data_cutoff,
                    snapshot.weighted_event_score,
                    snapshot.confidence,
                    snapshot.event_count,
                    snapshot.positive_event_count,
                    snapshot.negative_event_count,
                    snapshot.neutral_event_count,
                    snapshot.propagation_heat,
                    _json_dump(snapshot.top_positive_events),
                    _json_dump(snapshot.top_negative_events),
                    _json_dump(snapshot.evidence_ids),
                    _json_dump(snapshot.model_call_ids),
                    _json_dump(snapshot.missing_fields),
                    _json_dump(
                        [flag.value for flag in snapshot.risk_flags]
                    ),
                    snapshot.input_snapshot_hash,
                    True,
                    snapshot.algorithm_version,
                    snapshot.generated_at,
                ],
            )
        persisted = self.get_symbol_snapshot(snapshot.snapshot_id)
        if persisted is None:
            persisted = self.latest_symbol_snapshot(
                symbol=snapshot.symbol,
                data_cutoff=snapshot.data_cutoff,
                mode=snapshot.analysis_mode,
            )
        if persisted is None:
            raise RuntimeError("sentiment symbol snapshot was not persisted")
        return persisted

    def get_symbol_snapshot(
        self,
        snapshot_id: str,
    ) -> SentimentSymbolSnapshot | None:
        with get_connection() as connection:
            row = connection.execute(
                self._symbol_select() + " WHERE snapshot_id = ?",
                [snapshot_id],
            ).fetchone()
        return None if row is None else self._symbol_from_row(row)

    def latest_symbol_snapshot(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        mode: AnalysisMode | None = None,
    ) -> SentimentSymbolSnapshot | None:
        clauses = ["symbol = ?", "data_cutoff <= ?"]
        parameters: list[Any] = [symbol, data_cutoff]
        if mode is not None:
            clauses.append("analysis_mode = ?")
            parameters.append(mode.value)
        with get_connection() as connection:
            row = connection.execute(
                self._symbol_select()
                + f"""
                  WHERE {' AND '.join(clauses)}
                  ORDER BY data_cutoff DESC, generated_at DESC
                  LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else self._symbol_from_row(row)

    @staticmethod
    def _market_from_row(row: tuple[Any, ...]) -> MarketBreadthSnapshot:
        return MarketBreadthSnapshot(
            market_snapshot_id=row[0],
            analysis_mode=row[1],
            data_cutoff=row[2],
            score=row[3],
            confidence=row[4],
            universe_size=row[5],
            advances=row[6],
            declines=row[7],
            flats=row[8],
            limit_ups=row[9],
            limit_downs=row[10],
            broken_limit_ups=row[11],
            broken_limit_up_rate=row[12],
            max_limit_up_streak=row[13],
            total_amount=row[14],
            amount_vs_20d_average=row[15],
            advance_amount_ratio=row[16],
            decline_amount_ratio=row[17],
            sector_advance_ratios=_json_load(row[18]),
            sector_diffusion=row[19],
            high_level_strength=row[20],
            temperature=row[21],
            missing_fields=_json_load(row[22]),
            risk_flags=_json_load(row[23]),
            evidence_ids=_json_load(row[24]),
            input_snapshot_hash=row[25],
            algorithm_version=row[26],
            generated_at=row[27],
            shadow_mode=True,
        )

    @staticmethod
    def _market_select() -> str:
        return """
            SELECT market_snapshot_id, analysis_mode, data_cutoff,
                   score, confidence, universe_size, advances, declines,
                   flats, limit_ups, limit_downs, broken_limit_ups,
                   broken_limit_up_rate, max_limit_up_streak,
                   total_amount, amount_vs_20d_average,
                   advance_amount_ratio, decline_amount_ratio,
                   sector_advance_ratios_json, sector_diffusion,
                   high_level_strength, temperature,
                   missing_fields_json, risk_flags_json,
                   evidence_ids_json, input_snapshot_hash,
                   algorithm_version, generated_at
            FROM sentiment_market_snapshots
        """

    def save_market_snapshot(
        self,
        snapshot: MarketBreadthSnapshot,
    ) -> MarketBreadthSnapshot:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_market_snapshots (
                    market_snapshot_id, analysis_mode, data_cutoff,
                    score, confidence, universe_size, advances, declines,
                    flats, limit_ups, limit_downs, broken_limit_ups,
                    broken_limit_up_rate, max_limit_up_streak,
                    total_amount, amount_vs_20d_average,
                    advance_amount_ratio, decline_amount_ratio,
                    sector_advance_ratios_json, sector_diffusion,
                    high_level_strength, temperature,
                    missing_fields_json, risk_flags_json,
                    evidence_ids_json, input_snapshot_hash,
                    algorithm_version, generated_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    snapshot.market_snapshot_id,
                    snapshot.analysis_mode.value,
                    snapshot.data_cutoff,
                    snapshot.score,
                    snapshot.confidence,
                    snapshot.universe_size,
                    snapshot.advances,
                    snapshot.declines,
                    snapshot.flats,
                    snapshot.limit_ups,
                    snapshot.limit_downs,
                    snapshot.broken_limit_ups,
                    snapshot.broken_limit_up_rate,
                    snapshot.max_limit_up_streak,
                    snapshot.total_amount,
                    snapshot.amount_vs_20d_average,
                    snapshot.advance_amount_ratio,
                    snapshot.decline_amount_ratio,
                    _json_dump(snapshot.sector_advance_ratios),
                    snapshot.sector_diffusion,
                    snapshot.high_level_strength,
                    snapshot.temperature,
                    _json_dump(snapshot.missing_fields),
                    _json_dump(
                        [flag.value for flag in snapshot.risk_flags]
                    ),
                    _json_dump(snapshot.evidence_ids),
                    snapshot.input_snapshot_hash,
                    snapshot.algorithm_version,
                    snapshot.generated_at,
                ],
            )
        persisted = self.get_market_snapshot(snapshot.market_snapshot_id)
        if persisted is None:
            persisted = self.latest_market_snapshot(
                data_cutoff=snapshot.data_cutoff,
                mode=snapshot.analysis_mode,
            )
        if persisted is None:
            raise RuntimeError("sentiment market snapshot was not persisted")
        return persisted

    def get_market_snapshot(
        self,
        market_snapshot_id: str,
    ) -> MarketBreadthSnapshot | None:
        with get_connection() as connection:
            row = connection.execute(
                self._market_select() + " WHERE market_snapshot_id = ?",
                [market_snapshot_id],
            ).fetchone()
        return None if row is None else self._market_from_row(row)

    def latest_market_snapshot(
        self,
        *,
        data_cutoff: datetime,
        mode: AnalysisMode | None = None,
    ) -> MarketBreadthSnapshot | None:
        clauses = ["data_cutoff <= ?"]
        parameters: list[Any] = [data_cutoff]
        if mode is not None:
            clauses.append("analysis_mode = ?")
            parameters.append(mode.value)
        with get_connection() as connection:
            row = connection.execute(
                self._market_select()
                + f"""
                  WHERE {' AND '.join(clauses)}
                  ORDER BY data_cutoff DESC, generated_at DESC
                  LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else self._market_from_row(row)

    def market_daily_records(
        self,
        *,
        data_cutoff: datetime,
    ) -> list[dict[str, Any]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT canonical_record_id, symbol, event_time,
                       data_cutoff, source_record_ids_json, payload_json
                FROM canonical_market_records
                WHERE data_type = 'daily_bar'
                  AND event_time <= ?
                  AND data_cutoff <= ?
                  AND verification_status <> 'CONFLICT'
                ORDER BY event_time, symbol, canonical_record_id
                """,
                [data_cutoff, data_cutoff],
            ).fetchall()
        return [
            {
                "canonical_record_id": row[0],
                "symbol": row[1],
                "event_time": row[2],
                "data_cutoff": row[3],
                "source_record_ids": list(_json_load(row[4])),
                "payload": _json_load(row[5]),
            }
            for row in rows
        ]

    def save_evaluation(
        self,
        evaluation: SentimentEvaluation,
    ) -> SentimentEvaluation:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_evaluations (
                    evaluation_id, snapshot_id, symbol, event_time,
                    alert_or_snapshot_time, data_cutoff,
                    sentiment_score, confidence, return_1d, return_3d,
                    return_5d, max_rise, max_drawdown, was_suspended,
                    was_limit_up, was_limit_down, data_complete,
                    evidence_ids_json, missing_fields_json,
                    generated_at, algorithm_version
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    evaluation.evaluation_id,
                    evaluation.snapshot_id,
                    evaluation.symbol,
                    evaluation.event_time,
                    evaluation.alert_or_snapshot_time,
                    evaluation.data_cutoff,
                    evaluation.sentiment_score,
                    evaluation.confidence,
                    evaluation.return_1d,
                    evaluation.return_3d,
                    evaluation.return_5d,
                    evaluation.max_rise,
                    evaluation.max_drawdown,
                    evaluation.was_suspended,
                    evaluation.was_limit_up,
                    evaluation.was_limit_down,
                    evaluation.data_complete,
                    _json_dump(evaluation.evidence_ids),
                    _json_dump(evaluation.missing_fields),
                    evaluation.generated_at,
                    evaluation.algorithm_version,
                ],
            )
        return evaluation


__all__ = ["SentimentRepository"]
