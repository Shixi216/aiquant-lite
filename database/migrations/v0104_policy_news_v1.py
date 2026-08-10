from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0104_policy_news_v1"
SCHEMA_VERSION = "policy-news-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS policy_news_event_analyses (
        policy_analysis_id VARCHAR PRIMARY KEY,
        event_cluster_id VARCHAR NOT NULL,
        event_category VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        direction BIGINT NOT NULL CHECK (direction IN (-1, 0, 1)),
        intensity DOUBLE NOT NULL CHECK (intensity BETWEEN 0 AND 1),
        model_confidence DOUBLE NOT NULL
            CHECK (model_confidence BETWEEN 0 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        fact_type VARCHAR NOT NULL,
        source_level VARCHAR NOT NULL,
        source_authority_weight DOUBLE NOT NULL
            CHECK (source_authority_weight BETWEEN 0 AND 1),
        freshness_weight DOUBLE NOT NULL
            CHECK (freshness_weight BETWEEN 0 AND 1),
        implementation_status VARCHAR NOT NULL,
        implementation_confidence DOUBLE NOT NULL
            CHECK (implementation_confidence BETWEEN 0 AND 1),
        implementation_weight DOUBLE NOT NULL
            CHECK (implementation_weight BETWEEN 0 AND 1),
        verification_status VARCHAR NOT NULL,
        verification_weight DOUBLE NOT NULL
            CHECK (verification_weight BETWEEN 0 AND 1),
        relevance_weight DOUBLE NOT NULL
            CHECK (relevance_weight BETWEEN 0 AND 1),
        policy_news_score DOUBLE NOT NULL
            CHECK (policy_news_score BETWEEN -1 AND 1),
        impact_horizon VARCHAR NOT NULL,
        text_completeness VARCHAR NOT NULL,
        affected_symbols_json JSON NOT NULL,
        affected_sectors_json JSON NOT NULL,
        symbol_relevance_json JSON NOT NULL,
        sector_relevance_json JSON NOT NULL,
        summary VARCHAR NOT NULL,
        key_facts_json JSON NOT NULL,
        amounts_json JSON NOT NULL,
        dates_json JSON NOT NULL,
        entities_json JSON NOT NULL,
        conditions_json JSON NOT NULL,
        shared_sentiment_analysis_ids_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        extractor_version VARCHAR NOT NULL,
        scorer_version VARCHAR NOT NULL,
        prompt_version VARCHAR NOT NULL,
        mapping_version VARCHAR NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        event_time TIMESTAMPTZ NOT NULL,
        publication_time TIMESTAMPTZ,
        data_available_time TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        implementation_time TIMESTAMPTZ,
        termination_time TIMESTAMPTZ,
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        UNIQUE (
            event_cluster_id, data_cutoff, scorer_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policy_news_symbol_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        event_count BIGINT NOT NULL CHECK (event_count >= 0),
        positive_event_count BIGINT NOT NULL CHECK (positive_event_count >= 0),
        negative_event_count BIGINT NOT NULL CHECK (negative_event_count >= 0),
        neutral_event_count BIGINT NOT NULL CHECK (neutral_event_count >= 0),
        weighted_policy_score DOUBLE NOT NULL
            CHECK (weighted_policy_score BETWEEN -1 AND 1),
        high_authority_event_count BIGINT NOT NULL CHECK (
            high_authority_event_count >= 0
        ),
        implemented_event_count BIGINT NOT NULL CHECK (
            implemented_event_count >= 0
        ),
        conflict_event_count BIGINT NOT NULL CHECK (conflict_event_count >= 0),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        horizon_scores_json JSON NOT NULL,
        top_positive_event_ids_json JSON NOT NULL,
        top_negative_event_ids_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        shared_sentiment_event_ids_json JSON NOT NULL,
        missing_fields_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        UNIQUE (
            symbol, analysis_mode, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policy_news_sector_snapshots (
        snapshot_id VARCHAR PRIMARY KEY,
        sector VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (analysis_mode IN ('SCREENING', 'RESEARCH', 'DECISION')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        event_count BIGINT NOT NULL CHECK (event_count >= 0),
        positive_event_count BIGINT NOT NULL CHECK (positive_event_count >= 0),
        negative_event_count BIGINT NOT NULL CHECK (negative_event_count >= 0),
        neutral_event_count BIGINT NOT NULL CHECK (neutral_event_count >= 0),
        weighted_policy_score DOUBLE NOT NULL
            CHECK (weighted_policy_score BETWEEN -1 AND 1),
        high_authority_event_count BIGINT NOT NULL CHECK (
            high_authority_event_count >= 0
        ),
        implemented_event_count BIGINT NOT NULL CHECK (
            implemented_event_count >= 0
        ),
        conflict_event_count BIGINT NOT NULL CHECK (conflict_event_count >= 0),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        horizon_scores_json JSON NOT NULL,
        top_positive_event_ids_json JSON NOT NULL,
        top_negative_event_ids_json JSON NOT NULL,
        evidence_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        shared_sentiment_event_ids_json JSON NOT NULL,
        missing_fields_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        shadow_mode BOOLEAN NOT NULL CHECK (shadow_mode = TRUE),
        UNIQUE (
            sector, analysis_mode, data_cutoff, algorithm_version,
            input_snapshot_hash
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policy_news_evaluations (
        evaluation_id VARCHAR PRIMARY KEY,
        snapshot_id VARCHAR NOT NULL,
        symbol VARCHAR,
        sector VARCHAR,
        event_time TIMESTAMPTZ,
        publication_time TIMESTAMPTZ,
        implementation_status_at_analysis VARCHAR NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        policy_news_score DOUBLE NOT NULL
            CHECK (policy_news_score BETWEEN -1 AND 1),
        confidence DOUBLE NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        return_1d DOUBLE,
        return_3d DOUBLE,
        return_5d DOUBLE,
        return_20d DOUBLE,
        max_rise DOUBLE,
        max_drawdown DOUBLE,
        actually_implemented BOOLEAN,
        terminated_or_retracted BOOLEAN,
        data_complete BOOLEAN NOT NULL,
        evidence_ids_json JSON NOT NULL,
        missing_fields_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        UNIQUE (snapshot_id, algorithm_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS policy_news_backfill_runs (
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
    CREATE TABLE IF NOT EXISTS policy_news_backfill_items (
        backfill_run_id VARCHAR NOT NULL,
        event_cluster_id VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        policy_analysis_id VARCHAR,
        event_category VARCHAR,
        error_type VARCHAR,
        error_message VARCHAR,
        processed_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (backfill_run_id, event_cluster_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_news_event_cluster
    ON policy_news_event_analyses (event_cluster_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_news_event_input_hash
    ON policy_news_event_analyses (input_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_news_event_generated
    ON policy_news_event_analyses (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_news_symbol_cutoff
    ON policy_news_symbol_snapshots (symbol, data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_policy_news_sector_cutoff
    ON policy_news_sector_snapshots (sector, data_cutoff)
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
