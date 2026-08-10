from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0103_sentiment_v1"
SCHEMA_VERSION = "sentiment-v1"

MIGRATION_STATEMENTS = (
    """
    ALTER TABLE model_calls
    ADD COLUMN IF NOT EXISTS prompt_version VARCHAR
    """,
    """
    ALTER TABLE model_calls
    ADD COLUMN IF NOT EXISTS input_hash VARCHAR
    """,
    """
    ALTER TABLE model_calls
    ADD COLUMN IF NOT EXISTS retry_count BIGINT DEFAULT 0
    """,
    """
    ALTER TABLE model_calls
    ADD COLUMN IF NOT EXISTS schema_validation VARCHAR
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_event_analyses (
        sentiment_analysis_id VARCHAR PRIMARY KEY,
        event_cluster_id VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        direction BIGINT NOT NULL CHECK (direction IN (-1, 0, 1)),
        intensity DOUBLE NOT NULL CHECK (intensity BETWEEN 0 AND 1),
        model_confidence DOUBLE NOT NULL
            CHECK (model_confidence BETWEEN 0 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        source_level VARCHAR NOT NULL,
        source_quality_weight DOUBLE NOT NULL
            CHECK (source_quality_weight BETWEEN 0 AND 1),
        freshness_weight DOUBLE NOT NULL
            CHECK (freshness_weight BETWEEN 0 AND 1),
        verification_status VARCHAR NOT NULL,
        verification_weight DOUBLE NOT NULL
            CHECK (verification_weight BETWEEN 0 AND 1),
        relevance_weight DOUBLE NOT NULL
            CHECK (relevance_weight BETWEEN 0 AND 1),
        relevance_by_symbol_json JSON NOT NULL,
        event_score DOUBLE NOT NULL CHECK (event_score BETWEEN -1 AND 1),
        impact_horizon VARCHAR NOT NULL,
        fact_type VARCHAR NOT NULL,
        affected_symbols_json JSON NOT NULL,
        affected_sectors_json JSON NOT NULL,
        summary VARCHAR NOT NULL,
        risk_flags_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        extractor_version VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        prompt_version VARCHAR NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        propagation_heat DOUBLE NOT NULL CHECK (propagation_heat >= 0),
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        UNIQUE (
            event_cluster_id, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_symbol_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        score DOUBLE NOT NULL CHECK (score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        event_count BIGINT NOT NULL CHECK (event_count >= 0),
        positive_event_count BIGINT NOT NULL CHECK (positive_event_count >= 0),
        negative_event_count BIGINT NOT NULL CHECK (negative_event_count >= 0),
        neutral_event_count BIGINT NOT NULL CHECK (neutral_event_count >= 0),
        propagation_heat DOUBLE NOT NULL CHECK (propagation_heat >= 0),
        top_positive_event_ids_json JSON NOT NULL,
        top_negative_event_ids_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        missing_fields_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        algorithm_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (
            symbol, analysis_mode, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_market_snapshots (
        market_snapshot_id VARCHAR PRIMARY KEY,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        score DOUBLE NOT NULL CHECK (score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        universe_size BIGINT NOT NULL CHECK (universe_size >= 0),
        advances BIGINT,
        declines BIGINT,
        flats BIGINT,
        limit_ups BIGINT,
        limit_downs BIGINT,
        broken_limit_ups BIGINT,
        broken_limit_up_rate DOUBLE,
        max_limit_up_streak BIGINT,
        total_amount DOUBLE,
        amount_vs_20d_average DOUBLE,
        advance_amount_ratio DOUBLE,
        decline_amount_ratio DOUBLE,
        sector_advance_ratios_json JSON NOT NULL,
        sector_diffusion DOUBLE,
        high_level_strength DOUBLE,
        temperature VARCHAR,
        missing_fields_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (
            analysis_mode, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_evaluations (
        evaluation_id VARCHAR PRIMARY KEY,
        snapshot_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        event_time TIMESTAMPTZ,
        alert_or_snapshot_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        sentiment_score DOUBLE NOT NULL
            CHECK (sentiment_score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        return_1d DOUBLE,
        return_3d DOUBLE,
        return_5d DOUBLE,
        max_rise DOUBLE,
        max_drawdown DOUBLE,
        was_suspended BOOLEAN,
        was_limit_up BOOLEAN,
        was_limit_down BOOLEAN,
        data_complete BOOLEAN NOT NULL,
        evidence_ids_json JSON NOT NULL,
        missing_fields_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        UNIQUE (snapshot_id, algorithm_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_backfill_runs (
        backfill_run_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        filters_json JSON NOT NULL,
        status VARCHAR NOT NULL,
        processed_count BIGINT NOT NULL,
        success_count BIGINT NOT NULL,
        skipped_count BIGINT NOT NULL,
        failed_count BIGINT NOT NULL,
        model_call_count BIGINT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        report_path VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sentiment_backfill_items (
        backfill_run_id VARCHAR NOT NULL,
        event_cluster_id VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        sentiment_analysis_id VARCHAR,
        error_type VARCHAR,
        error_message VARCHAR,
        processed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (backfill_run_id, event_cluster_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_event_cluster
    ON sentiment_event_analyses (event_cluster_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_event_generated
    ON sentiment_event_analyses (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_event_input_hash
    ON sentiment_event_analyses (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_cutoff
    ON sentiment_symbol_snapshots (symbol, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_symbol_input_hash
    ON sentiment_symbol_snapshots (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_market_cutoff
    ON sentiment_market_snapshots (data_cutoff)
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    payload += f"\n{SCHEMA_VERSION}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def apply_migration(connection: duckdb.DuckDBPyConnection) -> bool:
    checksum = _checksum()
    connection.execute("BEGIN TRANSACTION")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                migration_id VARCHAR PRIMARY KEY,
                checksum VARCHAR NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        existing = connection.execute(
            """
            SELECT checksum
            FROM schema_migrations
            WHERE migration_id = ?
            """,
            [MIGRATION_ID],
        ).fetchone()
        if existing is not None:
            if existing[0] != checksum:
                raise RuntimeError(
                    f"migration checksum mismatch for {MIGRATION_ID}"
                )
            connection.execute("COMMIT")
            return False

        for statement in MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations (
                migration_id, checksum, applied_at
            )
            VALUES (?, ?, ?)
            """,
            [MIGRATION_ID, checksum, datetime.now().astimezone()],
        )
        connection.execute("COMMIT")
        return True
    except Exception:
        connection.execute("ROLLBACK")
        raise


__all__ = [
    "MIGRATION_ID",
    "MIGRATION_STATEMENTS",
    "SCHEMA_VERSION",
    "apply_migration",
]
