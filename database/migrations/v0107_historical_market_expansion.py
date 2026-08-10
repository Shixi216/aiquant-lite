from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0107_historical_market_expansion"
SCHEMA_VERSION = "historical-market-expansion-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS trading_calendar_days (
        provider VARCHAR NOT NULL,
        calendar_date DATE NOT NULL,
        is_trading_day BOOLEAN NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        content_hash VARCHAR NOT NULL,
        source_metadata_json JSON NOT NULL,
        PRIMARY KEY (provider, calendar_date)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS canonical_historical_bars (
        bar_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        trade_date DATE NOT NULL,
        event_time TIMESTAMPTZ NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        adjustment_type VARCHAR NOT NULL
            CHECK (
                adjustment_type IN (
                    'RAW', 'FORWARD_ADJUSTED', 'BACKWARD_ADJUSTED'
                )
            ),
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        close DOUBLE,
        volume DOUBLE,
        amount DOUBLE,
        volume_unit VARCHAR NOT NULL,
        amount_unit VARCHAR NOT NULL,
        primary_source VARCHAR NOT NULL,
        source_record_ids_json JSON NOT NULL,
        verification_source_ids_json JSON NOT NULL,
        verification_status VARCHAR NOT NULL
            CHECK (
                verification_status IN (
                    'VERIFIED', 'CONFLICT', 'SINGLE_SOURCE'
                )
            ),
        confidence DOUBLE NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
        content_hash VARCHAR NOT NULL,
        algorithm_version VARCHAR NOT NULL,
        raw_payload_json JSON NOT NULL,
        UNIQUE (symbol, trade_date, adjustment_type, content_hash)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_backfill_runs (
        run_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        provider VARCHAR NOT NULL,
        fallback_providers_json JSON NOT NULL,
        selection_strategy VARCHAR NOT NULL,
        requested_symbols_json JSON NOT NULL,
        requested_date_range_json JSON NOT NULL,
        target_trading_days BIGINT NOT NULL,
        adjustment_type VARCHAR NOT NULL,
        eligible_symbol_count BIGINT NOT NULL,
        completed_symbol_count BIGINT NOT NULL,
        successful_symbol_count BIGINT NOT NULL,
        skipped_symbol_count BIGINT NOT NULL,
        failed_symbol_count BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        request_budget BIGINT NOT NULL,
        concurrency BIGINT NOT NULL,
        max_retries BIGINT NOT NULL,
        retry_count BIGINT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        status VARCHAR NOT NULL
            CHECK (
                status IN (
                    'PENDING', 'RUNNING', 'PARTIAL', 'SUCCESS', 'FAILED',
                    'PAUSED_BUDGET', 'PAUSED_RATE_LIMIT', 'CANCELLED'
                )
            ),
        resume_cursor BIGINT NOT NULL,
        database_bytes_before BIGINT NOT NULL,
        database_bytes_after BIGINT,
        persisted_raw_count BIGINT NOT NULL,
        persisted_canonical_count BIGINT NOT NULL,
        report_path VARCHAR,
        request_json JSON NOT NULL,
        error_summary_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_backfill_shards (
        shard_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        shard_index BIGINT NOT NULL,
        start_symbol VARCHAR,
        end_symbol VARCHAR,
        symbol_count BIGINT NOT NULL,
        requested_date_range_json JSON NOT NULL,
        status VARCHAR NOT NULL
            CHECK (
                status IN (
                    'PENDING', 'RUNNING', 'PARTIAL', 'SUCCESS', 'FAILED',
                    'PAUSED_BUDGET', 'PAUSED_RATE_LIMIT', 'CANCELLED'
                )
            ),
        resume_cursor BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        successful_symbol_count BIGINT NOT NULL,
        skipped_symbol_count BIGINT NOT NULL,
        failed_symbol_count BIGINT NOT NULL,
        write_elapsed_seconds DOUBLE,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        UNIQUE (run_id, shard_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS historical_backfill_items (
        run_id VARCHAR NOT NULL,
        shard_id VARCHAR NOT NULL,
        item_index BIGINT NOT NULL,
        symbol VARCHAR NOT NULL,
        list_date DATE,
        eligibility_status VARCHAR NOT NULL,
        expected_trading_days BIGINT NOT NULL,
        observed_trading_days BIGINT NOT NULL,
        provider_used VARCHAR,
        adjustment_type VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        attempt_count BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        raw_record_count BIGINT NOT NULL,
        canonical_record_count BIGINT NOT NULL,
        database_growth_bytes BIGINT,
        fetch_elapsed_seconds DOUBLE,
        write_elapsed_seconds DOUBLE,
        started_at TIMESTAMPTZ,
        completed_at TIMESTAMPTZ,
        error_type VARCHAR,
        error_message VARCHAR,
        payload_json JSON NOT NULL,
        PRIMARY KEY (run_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_request_audits (
        audit_id VARCHAR PRIMARY KEY,
        run_id VARCHAR,
        shard_id VARCHAR,
        symbol VARCHAR,
        provider VARCHAR NOT NULL,
        capability VARCHAR NOT NULL,
        attempt BIGINT NOT NULL,
        request_started_at TIMESTAMPTZ NOT NULL,
        request_completed_at TIMESTAMPTZ NOT NULL,
        status VARCHAR NOT NULL,
        record_count BIGINT NOT NULL,
        latency_ms BIGINT NOT NULL,
        rate_limited BOOLEAN NOT NULL,
        error_type VARCHAR,
        error_message VARCHAR,
        request_hash VARCHAR NOT NULL,
        request_parameters_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS collection_window_audits (
        audit_id VARCHAR PRIMARY KEY,
        run_id VARCHAR,
        data_kind VARCHAR NOT NULL
            CHECK (data_kind IN ('ANNOUNCEMENT', 'FINANCE_NEWS')),
        provider VARCHAR NOT NULL,
        window_start TIMESTAMPTZ NOT NULL,
        window_end TIMESTAMPTZ NOT NULL,
        provider_window_capability VARCHAR NOT NULL,
        batch_key VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        result_count BIGINT NOT NULL,
        full_text_count BIGINT NOT NULL,
        metadata_only_count BIGINT NOT NULL,
        duplicate_source_count BIGINT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NOT NULL,
        error_type VARCHAR,
        error_message VARCHAR,
        payload_json JSON NOT NULL,
        UNIQUE (data_kind, provider, batch_key, started_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_data_update_runs (
        run_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        data_cutoff TIMESTAMPTZ NOT NULL,
        status VARCHAR NOT NULL,
        request_budget BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        model_call_count BIGINT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        payload_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS daily_data_update_steps (
        run_id VARCHAR NOT NULL,
        step_index BIGINT NOT NULL,
        step_name VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        processed_count BIGINT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ NOT NULL,
        payload_json JSON NOT NULL,
        PRIMARY KEY (run_id, step_index)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_calendar_date
    ON trading_calendar_days (calendar_date, is_trading_day)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_historical_bar_symbol_date
    ON canonical_historical_bars (
        symbol, trade_date, adjustment_type, data_cutoff
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_historical_bar_status
    ON canonical_historical_bars (
        verification_status, adjustment_type, trade_date
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_history_run_status
    ON historical_backfill_runs (status, started_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_history_shard_run
    ON historical_backfill_shards (run_id, shard_id, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_history_item_symbol
    ON historical_backfill_items (symbol, status, run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_provider_audit
    ON provider_request_audits (
        provider, status, run_id, shard_id, symbol
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_collection_window
    ON collection_window_audits (
        data_kind, provider, window_start, window_end, status
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_daily_update_status
    ON daily_data_update_runs (status, data_cutoff)
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
            "SELECT checksum FROM schema_migrations WHERE migration_id = ?",
            [MIGRATION_ID],
        ).fetchone()
        if existing is not None:
            if existing[0] != checksum:
                raise RuntimeError(
                    f"Migration checksum mismatch for {MIGRATION_ID}"
                )
            connection.execute("COMMIT")
            return False
        for statement in MIGRATION_STATEMENTS:
            connection.execute(statement)
        connection.execute(
            """
            INSERT INTO schema_migrations (migration_id, checksum, applied_at)
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
