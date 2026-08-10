from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from config.settings import settings
from database.db import get_connection, initialize_database
from trading.research.capital_flow.models import FinancingRecord, MarketBar
from trading.research.capital_flow.schemas import (
    CapitalFlowEvaluation,
    CapitalFlowMarketSnapshot,
    CapitalFlowSectorSnapshot,
    CapitalFlowSymbolSnapshot,
)
from trading.research.capital_flow.units import RateUnit, normalize_rate, resolve_unit_values
from trading.schemas import AnalysisMode


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CapitalFlowRepository:
    """One-connection bulk reads and append/idempotent shadow writes."""

    def __init__(self) -> None:
        initialize_database()

    def market_bars(
        self,
        *,
        data_cutoff: datetime,
        symbols: list[str] | None = None,
    ) -> dict[str, list[MarketBar]]:
        symbol_clause = ""
        parameters: list[Any] = [
            settings.history_default_adjustment,
            data_cutoff,
            data_cutoff,
            data_cutoff,
            data_cutoff,
            data_cutoff,
        ]
        if symbols:
            placeholders = ",".join("?" for _ in symbols)
            symbol_clause = f"AND symbol IN ({placeholders})"
            parameters = [
                settings.history_default_adjustment,
                data_cutoff,
                data_cutoff,
                data_cutoff,
                *symbols,
                data_cutoff,
                data_cutoff,
                *symbols,
            ]
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                WITH preferred AS (
                    SELECT
                        bar_id AS record_id,
                        symbol,
                        event_time,
                        data_cutoff,
                        source_record_ids_json,
                        open,
                        high,
                        low,
                        close,
                        volume,
                        amount
                    FROM canonical_historical_bars
                    WHERE adjustment_type = ?
                      AND event_time <= ?
                      AND data_available_time <= ?
                      AND data_cutoff <= ?
                      AND verification_status <> 'CONFLICT'
                      {symbol_clause}
                ),
                legacy AS (
                    SELECT
                        canonical_record_id AS record_id,
                        symbol,
                        event_time,
                        data_cutoff,
                        source_record_ids_json,
                        TRY_CAST(
                            json_extract(payload_json, '$.open') AS DOUBLE
                        ) AS open,
                        TRY_CAST(
                            json_extract(payload_json, '$.high') AS DOUBLE
                        ) AS high,
                        TRY_CAST(
                            json_extract(payload_json, '$.low') AS DOUBLE
                        ) AS low,
                        TRY_CAST(
                            json_extract(payload_json, '$.close') AS DOUBLE
                        ) AS close,
                        TRY_CAST(
                            json_extract(payload_json, '$.volume') AS DOUBLE
                        ) AS volume,
                        TRY_CAST(
                            json_extract(payload_json, '$.amount') AS DOUBLE
                        ) AS amount
                    FROM canonical_market_records
                    WHERE data_type = 'daily_bar'
                      AND event_time <= ?
                      AND data_cutoff <= ?
                      AND verification_status <> 'CONFLICT'
                      {symbol_clause}
                      AND NOT EXISTS (
                          SELECT 1
                          FROM preferred new_bar
                          WHERE new_bar.symbol =
                              canonical_market_records.symbol
                            AND CAST(
                                new_bar.event_time
                                    AT TIME ZONE 'Asia/Shanghai' AS DATE
                            ) = CAST(
                                canonical_market_records.event_time
                                    AT TIME ZONE 'Asia/Shanghai' AS DATE
                            )
                      )
                )
                SELECT * FROM preferred
                UNION ALL
                SELECT * FROM legacy
                ORDER BY symbol, event_time, record_id
                """,
                parameters,
            ).fetchall()
            source_ids = sorted(
                {
                    str(record_id)
                    for row in rows
                    for record_id in _load(row[4])
                }
            )
            raw: dict[str, tuple[str, dict[str, Any]]] = {}
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                raw_rows = connection.execute(
                    f"""
                    SELECT record_id, source_name, payload_json
                    FROM data_records
                    WHERE record_id IN ({placeholders})
                    """,
                    source_ids,
                ).fetchall()
                raw = {
                    row[0]: (row[1], _load(row[2]))
                    for row in raw_rows
                }
            sectors = self._sector_map(connection, data_cutoff=data_cutoff)
        output: dict[str, list[MarketBar]] = defaultdict(list)
        for row in rows:
            unit_values = []
            for record_id in _load(row[4]):
                source_payload = raw.get(str(record_id))
                if source_payload is None:
                    continue
                source, raw_payload = source_payload
                if (
                    source.casefold().startswith("akshare")
                    and raw_payload.get("turnover_rate") is not None
                ):
                    unit_values.append(
                        normalize_rate(
                            raw_payload["turnover_rate"],
                            RateUnit.PERCENT,
                        )
                    )
            turnover = resolve_unit_values(unit_values) if unit_values else None
            output[row[1]].append(
                MarketBar(
                    canonical_record_id=row[0],
                    symbol=row[1],
                    event_time=row[2],
                    data_cutoff=row[3],
                    open=_number(row[5]),
                    high=_number(row[6]),
                    low=_number(row[7]),
                    close=_number(row[8]),
                    volume=_number(row[9]),
                    amount=_number(row[10]),
                    turnover_rate=turnover.value if turnover else None,
                    float_shares=None,
                    sector=sectors.get(row[1]),
                    unit_risk_flags=tuple(
                        flag.value for flag in turnover.flags
                    ) if turnover else (),
                )
            )
        return dict(output)

    @staticmethod
    def _sector_map(
        connection: Any,
        *,
        data_cutoff: datetime,
    ) -> dict[str, str]:
        rows = connection.execute(
            """
            SELECT symbol, payload_json
            FROM data_records
            WHERE data_type = 'stock_basic'
              AND fetched_at <= ?
            QUALIFY row_number() OVER (
                PARTITION BY symbol
                ORDER BY
                    CASE WHEN source_name LIKE 'Tushare%' THEN 0 ELSE 1 END,
                    fetched_at DESC
            ) = 1
            """,
            [data_cutoff],
        ).fetchall()
        return {
            symbol: str(payload["industry"]).strip()
            for symbol, value in rows
            if (payload := _load(value)).get("industry")
        }

    def financing_records(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
    ) -> list[FinancingRecord]:
        # No verified financing source exists in 0.10.0. Missing is not zero.
        del symbol, data_cutoff
        return []

    @staticmethod
    def save_symbol_snapshot(
        snapshot: CapitalFlowSymbolSnapshot,
    ) -> CapitalFlowSymbolSnapshot:
        payload = snapshot.model_dump(mode="json")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO capital_flow_symbol_snapshots (
                    snapshot_id, symbol, analysis_mode, data_cutoff,
                    score, confidence, volume_score, amount_score,
                    turnover_score, price_volume_score, financing_score,
                    sector_flow_score, liquidity_score, volume_ratio_20d,
                    amount_ratio_20d, turnover_rate, price_volume_state,
                    financing_trend, amount_market_percentile,
                    missing_fields_json, risk_flags_json, evidence_ids_json,
                    input_snapshot_hash, algorithm_version, shadow_mode,
                    generated_at, payload_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    snapshot.snapshot_id,
                    snapshot.symbol,
                    snapshot.analysis_mode.value,
                    snapshot.data_cutoff,
                    snapshot.score,
                    snapshot.confidence,
                    snapshot.volume_score.value,
                    snapshot.amount_score.value,
                    snapshot.turnover_score.value,
                    snapshot.price_volume_score.value,
                    snapshot.financing_score.value,
                    snapshot.sector_flow_score.value,
                    snapshot.liquidity_score.value,
                    snapshot.volume_ratio_20d,
                    snapshot.amount_ratio_20d,
                    snapshot.turnover_rate,
                    snapshot.price_volume_state.value,
                    snapshot.financing_trend.value,
                    snapshot.amount_market_percentile,
                    _dump(snapshot.missing_fields),
                    _dump([flag.value for flag in snapshot.risk_flags]),
                    _dump(snapshot.evidence_ids),
                    snapshot.input_snapshot_hash,
                    snapshot.algorithm_version,
                    True,
                    snapshot.generated_at,
                    _dump(payload),
                ],
            )
            row = connection.execute(
                """
                SELECT payload_json
                FROM capital_flow_symbol_snapshots
                WHERE symbol = ? AND analysis_mode = ? AND data_cutoff = ?
                  AND algorithm_version = ? AND input_snapshot_hash = ?
                """,
                [
                    snapshot.symbol,
                    snapshot.analysis_mode.value,
                    snapshot.data_cutoff,
                    snapshot.algorithm_version,
                    snapshot.input_snapshot_hash,
                ],
            ).fetchone()
        return CapitalFlowSymbolSnapshot.model_validate(_load(row[0]))

    @staticmethod
    def latest_symbol_snapshot(
        *,
        symbol: str,
        data_cutoff: datetime,
        mode: AnalysisMode | None = None,
    ) -> CapitalFlowSymbolSnapshot | None:
        clauses = ["symbol = ?", "data_cutoff <= ?"]
        parameters: list[Any] = [symbol, data_cutoff]
        if mode is not None:
            clauses.append("analysis_mode = ?")
            parameters.append(mode.value)
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM capital_flow_symbol_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY data_cutoff DESC, generated_at DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return (
            None
            if row is None
            else CapitalFlowSymbolSnapshot.model_validate(_load(row[0]))
        )

    @staticmethod
    def save_sector_snapshot(
        snapshot: CapitalFlowSectorSnapshot,
    ) -> CapitalFlowSectorSnapshot:
        payload = snapshot.model_dump(mode="json")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO capital_flow_sector_snapshots (
                    snapshot_id, sector, analysis_mode, data_cutoff,
                    score, confidence, input_snapshot_hash,
                    algorithm_version, shadow_mode, generated_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    snapshot.snapshot_id,
                    snapshot.sector,
                    snapshot.analysis_mode.value,
                    snapshot.data_cutoff,
                    snapshot.score,
                    snapshot.confidence,
                    snapshot.input_snapshot_hash,
                    snapshot.algorithm_version,
                    True,
                    snapshot.generated_at,
                    _dump(payload),
                ],
            )
        return snapshot

    @staticmethod
    def latest_sector_snapshot(
        *,
        sector: str,
        data_cutoff: datetime,
        mode: AnalysisMode | None = None,
    ) -> CapitalFlowSectorSnapshot | None:
        clauses = ["sector = ?", "data_cutoff <= ?"]
        parameters: list[Any] = [sector, data_cutoff]
        if mode is not None:
            clauses.append("analysis_mode = ?")
            parameters.append(mode.value)
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM capital_flow_sector_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY data_cutoff DESC, generated_at DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else CapitalFlowSectorSnapshot.model_validate(_load(row[0]))

    @staticmethod
    def save_market_snapshot(
        snapshot: CapitalFlowMarketSnapshot,
    ) -> CapitalFlowMarketSnapshot:
        payload = snapshot.model_dump(mode="json")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO capital_flow_market_snapshots (
                    snapshot_id, analysis_mode, data_cutoff, score,
                    confidence, input_snapshot_hash, algorithm_version,
                    shadow_mode, generated_at, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    snapshot.snapshot_id,
                    snapshot.analysis_mode.value,
                    snapshot.data_cutoff,
                    snapshot.score,
                    snapshot.confidence,
                    snapshot.input_snapshot_hash,
                    snapshot.algorithm_version,
                    True,
                    snapshot.generated_at,
                    _dump(payload),
                ],
            )
        return snapshot

    @staticmethod
    def latest_market_snapshot(
        *,
        data_cutoff: datetime,
        mode: AnalysisMode | None = None,
    ) -> CapitalFlowMarketSnapshot | None:
        clauses = ["data_cutoff <= ?"]
        parameters: list[Any] = [data_cutoff]
        if mode is not None:
            clauses.append("analysis_mode = ?")
            parameters.append(mode.value)
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM capital_flow_market_snapshots
                WHERE {' AND '.join(clauses)}
                ORDER BY data_cutoff DESC, generated_at DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else CapitalFlowMarketSnapshot.model_validate(_load(row[0]))

    @staticmethod
    def get_symbol_snapshot(snapshot_id: str) -> CapitalFlowSymbolSnapshot | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM capital_flow_symbol_snapshots
                WHERE snapshot_id = ?
                """,
                [snapshot_id],
            ).fetchone()
        return None if row is None else CapitalFlowSymbolSnapshot.model_validate(_load(row[0]))

    @staticmethod
    def save_evaluation(
        evaluation: CapitalFlowEvaluation,
    ) -> CapitalFlowEvaluation:
        payload = evaluation.model_dump(mode="json")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO capital_flow_evaluations (
                    evaluation_id, snapshot_id, snapshot_time, symbol,
                    data_cutoff, capital_score, confidence,
                    price_volume_state, volume_ratio_20d, turnover_rate,
                    return_1d, return_3d, return_5d, return_20d,
                    max_rise, max_drawdown, was_limit_up, was_limit_down,
                    was_suspended, data_complete, evidence_ids_json,
                    missing_fields_json, generated_at, algorithm_version,
                    payload_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT DO NOTHING
                """,
                [
                    evaluation.evaluation_id,
                    evaluation.snapshot_id,
                    evaluation.snapshot_time,
                    evaluation.symbol,
                    evaluation.data_cutoff,
                    evaluation.capital_score,
                    evaluation.confidence,
                    evaluation.price_volume_state.value,
                    evaluation.volume_ratio_20d,
                    evaluation.turnover_rate,
                    evaluation.return_1d,
                    evaluation.return_3d,
                    evaluation.return_5d,
                    evaluation.return_20d,
                    evaluation.max_rise,
                    evaluation.max_drawdown,
                    evaluation.was_limit_up,
                    evaluation.was_limit_down,
                    evaluation.was_suspended,
                    evaluation.data_complete,
                    _dump(evaluation.evidence_ids),
                    _dump(evaluation.missing_fields),
                    evaluation.generated_at,
                    evaluation.algorithm_version,
                    _dump(payload),
                ],
            )
            row = connection.execute(
                """
                SELECT payload_json FROM capital_flow_evaluations
                WHERE snapshot_id = ? AND algorithm_version = ?
                """,
                [evaluation.snapshot_id, evaluation.algorithm_version],
            ).fetchone()
        return CapitalFlowEvaluation.model_validate(_load(row[0]))


__all__ = ["CapitalFlowRepository"]
