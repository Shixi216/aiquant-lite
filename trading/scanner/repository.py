from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

import pandas as pd

from database.db import get_connection, initialize_database
from trading.scanner.schemas import (
    ScannerCandidateCard,
    ScannerEvaluationItem,
    ScannerScanResponse,
)


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class FeatureSource:
    frame: pd.DataFrame
    input_snapshot_hash: str
    snapshot_id: str | None
    snapshot_time: datetime | None
    data_read_ms: float
    database_session_count: int = 1
    database_query_count: int = 1


_FEATURE_SQL = """
WITH chosen_universe AS (
    SELECT universe_version
    FROM stock_universe_versions
    WHERE data_cutoff <= $cutoff
      AND effective_at <= $cutoff
    ORDER BY effective_at DESC, universe_version DESC
    LIMIT 1
),
universe AS (
    SELECT stock.*
    FROM stock_universe AS stock
    JOIN chosen_universe USING (universe_version)
    WHERE stock.listing_status = 'ACTIVE'
      AND stock.data_available_time <= $cutoff
),
chosen_snapshot AS (
    SELECT snapshot_id, snapshot_time, data_cutoff, content_hash
    FROM market_snapshot_runs
    WHERE data_cutoff <= $cutoff
      AND snapshot_time <= $cutoff
      AND completed_at IS NOT NULL
    ORDER BY snapshot_time DESC, completed_at DESC, snapshot_id
    LIMIT 1
),
market AS (
    SELECT item.*, run.content_hash AS market_snapshot_hash
    FROM market_snapshot_items AS item
    JOIN chosen_snapshot AS run USING (snapshot_id)
),
ranked_bars AS (
    SELECT
        bar_id, symbol, trade_date, open, high, low, close, volume, amount,
        ROW_NUMBER() OVER (
            PARTITION BY symbol
            ORDER BY trade_date DESC, event_time DESC, bar_id DESC
        ) AS row_number
    FROM canonical_historical_bars
    WHERE adjustment_type = 'RAW'
      AND event_time <= $cutoff
      AND data_available_time <= $cutoff
      AND data_cutoff <= $cutoff
      AND verification_status <> 'CONFLICT'
),
history AS (
    SELECT
        symbol,
        COUNT(*) FILTER (WHERE row_number <= 61) AS history_count,
        LIST(bar_id ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_bar_ids,
        LIST(trade_date ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_dates,
        LIST(open ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_opens,
        LIST(high ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_highs,
        LIST(low ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_lows,
        LIST(close ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_closes,
        LIST(volume ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_volumes,
        LIST(amount ORDER BY trade_date) FILTER (
            WHERE row_number <= 61
        ) AS history_amounts
    FROM ranked_bars
    WHERE row_number <= 61
    GROUP BY symbol
),
industry AS (
    SELECT symbol, industry_name
    FROM stock_industry_memberships
    WHERE generated_at <= $cutoff
      AND (valid_from IS NULL OR valid_from <= $cutoff)
      AND (valid_to IS NULL OR valid_to >= $cutoff)
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY symbol
        ORDER BY generated_at DESC, membership_id
    ) = 1
),
fundamental AS (
    SELECT factor_id, symbol, score, confidence, risk_flags_json
    FROM factor_outputs
    WHERE factor_type = 'FUNDAMENTAL'
      AND data_cutoff <= $cutoff
      AND generated_at <= $cutoff
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY symbol
        ORDER BY data_cutoff DESC, generated_at DESC, factor_id
    ) = 1
),
sentiment AS (
    SELECT snapshot_id, symbol, score, confidence, risk_flags_json
    FROM sentiment_symbol_snapshots
    WHERE data_cutoff <= $cutoff
      AND generated_at <= $cutoff
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY symbol
        ORDER BY data_cutoff DESC, generated_at DESC, snapshot_id
    ) = 1
),
policy_news AS (
    SELECT
        snapshot_id, symbol, weighted_policy_score AS score,
        confidence, risk_flags_json
    FROM policy_news_symbol_snapshots
    WHERE data_cutoff <= $cutoff
      AND generated_at <= $cutoff
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY symbol
        ORDER BY data_cutoff DESC, generated_at DESC, snapshot_id
    ) = 1
),
persisted_capital AS (
    SELECT snapshot_id, symbol, score, confidence, risk_flags_json
    FROM capital_flow_symbol_snapshots
    WHERE data_cutoff <= $cutoff
      AND generated_at <= $cutoff
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY symbol
        ORDER BY data_cutoff DESC, generated_at DESC, snapshot_id
    ) = 1
),
shadow AS (
    SELECT composite_id, symbol, score, confidence, risk_flags_json
    FROM shadow_composite_snapshots
    WHERE data_cutoff <= $cutoff
      AND generated_at <= $cutoff
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY symbol
        ORDER BY data_cutoff DESC, generated_at DESC, composite_id
    ) = 1
)
SELECT
    stock.symbol,
    stock.exchange,
    stock.market,
    stock.board,
    stock.short_name,
    stock.company_name,
    stock.list_date,
    stock.is_st,
    stock.is_suspended AS universe_suspended,
    stock.price_limit_type,
    stock.universe_version,
    industry.industry_name AS industry,
    market.snapshot_id,
    market.price AS current_price,
    market.previous_close,
    market.open AS current_open,
    market.high AS current_high,
    market.low AS current_low,
    market.volume AS current_volume,
    market.amount,
    market.change_pct,
    market.turnover_rate,
    market.snapshot_time,
    market.market_snapshot_hash,
    market.item_status,
    market.is_suspended AS snapshot_suspended,
    history.history_count,
    history.history_bar_ids,
    history.history_dates,
    history.history_opens,
    history.history_highs,
    history.history_lows,
    history.history_closes,
    history.history_volumes,
    history.history_amounts,
    fundamental.factor_id AS fundamental_id,
    fundamental.score AS fundamental_score,
    fundamental.confidence AS fundamental_confidence,
    fundamental.risk_flags_json AS fundamental_risk_flags,
    sentiment.snapshot_id AS sentiment_id,
    sentiment.score AS sentiment_score,
    sentiment.confidence AS sentiment_confidence,
    sentiment.risk_flags_json AS sentiment_risk_flags,
    policy_news.snapshot_id AS policy_news_id,
    policy_news.score AS policy_news_score,
    policy_news.confidence AS policy_news_confidence,
    policy_news.risk_flags_json AS policy_news_risk_flags,
    persisted_capital.snapshot_id AS persisted_capital_id,
    persisted_capital.score AS persisted_capital_score,
    persisted_capital.confidence AS persisted_capital_confidence,
    persisted_capital.risk_flags_json AS persisted_capital_risk_flags,
    shadow.composite_id AS shadow_composite_id,
    shadow.score AS persisted_shadow_score,
    shadow.confidence AS persisted_shadow_confidence,
    shadow.risk_flags_json AS shadow_risk_flags
FROM universe AS stock
LEFT JOIN market USING (symbol)
LEFT JOIN history USING (symbol)
LEFT JOIN industry USING (symbol)
LEFT JOIN fundamental USING (symbol)
LEFT JOIN sentiment USING (symbol)
LEFT JOIN policy_news USING (symbol)
LEFT JOIN persisted_capital USING (symbol)
LEFT JOIN shadow USING (symbol)
ORDER BY stock.symbol
"""


class ScannerRepository:
    """One-session feature reads and append-only scanner audit persistence."""

    def __init__(self) -> None:
        initialize_database()

    def load_feature_source(self, data_cutoff: datetime) -> FeatureSource:
        started = perf_counter()
        with get_connection() as connection:
            frame = connection.execute(
                _FEATURE_SQL,
                {"cutoff": data_cutoff},
            ).fetchdf()
        elapsed = (perf_counter() - started) * 1000
        snapshot_id = (
            None
            if frame.empty or pd.isna(frame.iloc[0]["snapshot_id"])
            else str(frame.iloc[0]["snapshot_id"])
        )
        snapshot_time = (
            None
            if frame.empty or pd.isna(frame.iloc[0]["snapshot_time"])
            else frame.iloc[0]["snapshot_time"].to_pydatetime()
        )
        factor_ids: list[str] = []
        for column in (
            "fundamental_id",
            "sentiment_id",
            "policy_news_id",
            "persisted_capital_id",
            "shadow_composite_id",
        ):
            if column in frame:
                factor_ids.extend(
                    sorted(str(value) for value in frame[column].dropna().unique())
                )
        metadata = {
            "cutoff": data_cutoff.isoformat(),
            "universe_version": (
                None if frame.empty else str(frame.iloc[0]["universe_version"])
            ),
            "snapshot_id": snapshot_id,
            "market_snapshot_hash": (
                None
                if frame.empty or pd.isna(frame.iloc[0]["market_snapshot_hash"])
                else str(frame.iloc[0]["market_snapshot_hash"])
            ),
            "symbols": int(len(frame)),
            "history_rows": int(frame["history_count"].fillna(0).sum()),
            "history_max_date": max(
                (
                    values[-1]
                    for values in frame["history_dates"].dropna()
                    if len(values)
                ),
                default=None,
            ),
            "factor_ids": factor_ids,
        }
        snapshot_hash = hashlib.sha256(_dump(metadata).encode()).hexdigest()
        return FeatureSource(
            frame=frame,
            input_snapshot_hash=snapshot_hash,
            snapshot_id=snapshot_id,
            snapshot_time=snapshot_time,
            data_read_ms=elapsed,
        )

    def save_scan(self, response: ScannerScanResponse) -> bool:
        plan = response.parsed_query
        generated_at = datetime.now().astimezone()
        with get_connection() as connection:
            existing = connection.execute(
                "SELECT run_id FROM scanner_runs WHERE run_id = ?",
                [response.run_id],
            ).fetchone()
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO scanner_query_plans (
                        query_id, original_query, normalized_query,
                        analysis_mode, data_cutoff, parser_type,
                        parser_version, plan_hash, payload_json, generated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        plan.query_id,
                        plan.original_query,
                        plan.normalized_query,
                        plan.analysis_mode.value,
                        plan.data_cutoff,
                        plan.parser_type.value,
                        plan.parser_version,
                        plan.plan_hash,
                        plan.model_dump_json(),
                        plan.generated_at,
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO scanner_runs (
                        run_id, query_id, analysis_mode, data_cutoff,
                        snapshot_id, universe_count, scanned_count,
                        matched_count, returned_count, parser_type,
                        parser_version, scanner_version, plan_hash,
                        input_snapshot_hash, database_query_count,
                        network_request_count, model_call_count, elapsed_ms,
                        peak_memory_bytes, status, risk_flags_json,
                        payload_json, generated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        response.run_id,
                        plan.query_id,
                        response.analysis_mode.value,
                        response.data_cutoff,
                        (
                            response.candidates[0].evidence_summary.get(
                                "snapshot_id"
                            )
                            if response.candidates
                            else None
                        ),
                        response.universe_count,
                        response.scanned_count,
                        response.matched_count,
                        response.returned_count,
                        plan.parser_type.value,
                        plan.parser_version,
                        "market-scanner-v1",
                        plan.plan_hash,
                        response.input_snapshot_hash,
                        response.performance.database_query_count,
                        0,
                        0,
                        response.performance.elapsed_ms,
                        response.performance.peak_memory_bytes,
                        "SUCCESS",
                        _dump(
                            sorted(
                                {
                                    flag.value
                                    for candidate in response.candidates
                                    for flag in candidate.risk_flags
                                }
                            )
                        ),
                        response.model_dump_json(),
                        generated_at,
                    ],
                )
                for index, node in enumerate(plan.filters):
                    connection.execute(
                        """
                        INSERT INTO scanner_run_filters (
                            run_id, filter_index, filter_type,
                            filter_json, generated_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            response.run_id,
                            index,
                            node.node_type,
                            node.model_dump_json(),
                            generated_at,
                        ],
                    )
                for candidate in response.candidates:
                    connection.execute(
                        """
                        INSERT INTO scanner_candidates (
                            run_id, rank, symbol, scanner_score,
                            technical_score, capital_flow_score,
                            shadow_composite_score, composite_confidence,
                            factor_coverage, anomaly_types_json,
                            reason_codes_json, risk_flags_json,
                            evidence_ids_json, payload_json, generated_at
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            response.run_id,
                            candidate.rank,
                            candidate.symbol,
                            candidate.scanner_score,
                            candidate.technical_score,
                            candidate.capital_flow_score,
                            candidate.shadow_composite_score,
                            candidate.composite_confidence,
                            candidate.factor_coverage,
                            _dump(
                                [item.value for item in candidate.anomaly_types]
                            ),
                            _dump(candidate.reason_codes),
                            _dump(
                                [item.value for item in candidate.risk_flags]
                            ),
                            _dump(
                                candidate.evidence_summary.get(
                                    "evidence_ids",
                                    [],
                                )
                            ),
                            candidate.model_dump_json(),
                            generated_at,
                        ],
                    )
                    for reason_index, reason_code in enumerate(
                        candidate.reason_codes
                    ):
                        connection.execute(
                            """
                            INSERT INTO scanner_candidate_reasons (
                                run_id, symbol, reason_index, reason_code,
                                evidence_json, generated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT DO NOTHING
                            """,
                            [
                                response.run_id,
                                candidate.symbol,
                                reason_index,
                                reason_code,
                                _dump(candidate.evidence_summary),
                                generated_at,
                            ],
                        )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return existing is None

    def run_detail(self, run_id: str) -> dict[str, Any] | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json FROM scanner_runs WHERE run_id = ?
                """,
                [run_id],
            ).fetchone()
        return None if row is None else _load(row[0])

    def run_candidates(self, run_id: str) -> list[dict[str, Any]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM scanner_candidates
                WHERE run_id = ?
                ORDER BY rank
                """,
                [run_id],
            ).fetchall()
        return [_load(row[0]) for row in rows]

    def latest_symbol_candidate(self, symbol: str) -> dict[str, Any] | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM scanner_candidates
                WHERE symbol = ?
                ORDER BY generated_at DESC, run_id DESC
                LIMIT 1
                """,
                [symbol.upper()],
            ).fetchone()
        return None if row is None else _load(row[0])

    def evaluation_context(
        self,
        run_id: str,
    ) -> tuple[datetime, list[ScannerCandidateCard]] | None:
        with get_connection() as connection:
            run = connection.execute(
                "SELECT data_cutoff FROM scanner_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
            rows = connection.execute(
                """
                SELECT payload_json FROM scanner_candidates
                WHERE run_id = ? ORDER BY rank
                """,
                [run_id],
            ).fetchall()
        if run is None:
            return None
        return run[0], [
            ScannerCandidateCard.model_validate(_load(row[0])) for row in rows
        ]

    def future_closes(
        self,
        *,
        symbols: list[str],
        analysis_time: datetime,
        evaluated_at: datetime,
    ) -> dict[str, list[tuple[Any, float]]]:
        if not symbols:
            return {}
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT symbol, trade_date, close
                FROM canonical_historical_bars
                WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
                  AND adjustment_type = 'RAW'
                  AND event_time > ?
                  AND event_time <= ?
                  AND data_available_time <= ?
                  AND verification_status <> 'CONFLICT'
                  AND close > 0
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol, trade_date
                    ORDER BY generated_at DESC, bar_id
                ) = 1
                ORDER BY symbol, trade_date
                """,
                [symbols, analysis_time, evaluated_at, evaluated_at],
            ).fetchall()
        output: dict[str, list[tuple[Any, float]]] = {}
        for symbol, trade_date, close in rows:
            output.setdefault(symbol, []).append((trade_date, float(close)))
        return output

    def save_evaluations(
        self,
        *,
        analysis_time: datetime,
        evaluated_at: datetime,
        candidates: list[ScannerCandidateCard],
        items: list[ScannerEvaluationItem],
    ) -> int:
        candidates_by_symbol = {item.symbol: item for item in candidates}
        inserted = 0
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                for item in items:
                    candidate = candidates_by_symbol[item.symbol]
                    identity = hashlib.sha256(
                        _dump(
                            {
                                "run_id": item.run_id,
                                "symbol": item.symbol,
                                "evaluated_at": evaluated_at,
                            }
                        ).encode()
                    ).hexdigest()
                    existed = connection.execute(
                        """
                        SELECT evaluation_id FROM scanner_evaluations
                        WHERE evaluation_id = ?
                        """,
                        ["sev_" + identity[:24]],
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT INTO scanner_evaluations (
                            evaluation_id, run_id, symbol, candidate_rank,
                            scanner_score, anomaly_types_json,
                            factor_coverage, composite_confidence,
                            return_1d, return_3d, return_5d, return_20d,
                            maximum_upside, maximum_drawdown, is_suspended,
                            price_limit_up, price_limit_down, data_complete,
                            status, analysis_time, evaluated_at, payload_json,
                            generated_at
                        )
                        VALUES (
                            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?, ?, ?, ?
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        [
                            "sev_" + identity[:24],
                            item.run_id,
                            item.symbol,
                            item.rank,
                            item.scanner_score,
                            _dump(
                                [
                                    anomaly.value
                                    for anomaly in candidate.anomaly_types
                                ]
                            ),
                            candidate.factor_coverage,
                            candidate.composite_confidence,
                            item.return_1d,
                            item.return_3d,
                            item.return_5d,
                            item.return_20d,
                            item.maximum_upside,
                            item.maximum_drawdown,
                            item.is_suspended,
                            item.price_limit_up,
                            item.price_limit_down,
                            item.data_complete,
                            item.status,
                            analysis_time,
                            evaluated_at,
                            item.model_dump_json(),
                            datetime.now().astimezone(),
                        ],
                    )
                    inserted += existed is None
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return inserted


__all__ = ["FeatureSource", "ScannerRepository"]
