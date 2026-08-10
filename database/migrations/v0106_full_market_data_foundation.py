from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0106_full_market_data_foundation"
SCHEMA_VERSION = "full-market-data-foundation-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS provider_capabilities (
        provider VARCHAR NOT NULL,
        capability VARCHAR NOT NULL,
        available BOOLEAN NOT NULL,
        verified_at TIMESTAMPTZ NOT NULL,
        failure_reason VARCHAR,
        batch_supported BOOLEAN NOT NULL,
        maximum_batch_size BIGINT,
        rate_limit VARCHAR,
        requires_permission BOOLEAN NOT NULL,
        fallback_provider VARCHAR,
        metadata_json JSON NOT NULL,
        PRIMARY KEY (provider, capability, verified_at)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_universe_versions (
        universe_version VARCHAR PRIMARY KEY,
        content_hash VARCHAR NOT NULL UNIQUE,
        effective_at TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        total_count BIGINT NOT NULL,
        active_count BIGINT NOT NULL,
        source_names_json JSON NOT NULL,
        metadata_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_universe (
        symbol VARCHAR NOT NULL,
        exchange VARCHAR NOT NULL,
        market VARCHAR NOT NULL,
        board VARCHAR NOT NULL,
        security_type VARCHAR NOT NULL,
        company_name VARCHAR,
        short_name VARCHAR,
        list_date DATE,
        delist_date DATE,
        listing_status VARCHAR NOT NULL,
        is_st BOOLEAN NOT NULL,
        is_suspended BOOLEAN NOT NULL,
        currency VARCHAR NOT NULL,
        price_limit_type VARCHAR NOT NULL,
        primary_source VARCHAR NOT NULL,
        source_record_ids_json JSON NOT NULL,
        verification_status VARCHAR NOT NULL,
        source_differences_json JSON NOT NULL,
        data_available_time TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        universe_version VARCHAR NOT NULL,
        PRIMARY KEY (symbol, universe_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_aliases (
        alias_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        alias_name VARCHAR NOT NULL,
        alias_type VARCHAR NOT NULL,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        source VARCHAR NOT NULL,
        verification_status VARCHAR NOT NULL,
        normalized_alias VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        universe_version VARCHAR NOT NULL,
        UNIQUE (
            symbol, normalized_alias, alias_type, source,
            valid_from, universe_version
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS stock_industry_memberships (
        membership_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        industry_code VARCHAR,
        industry_name VARCHAR NOT NULL,
        industry_level VARCHAR NOT NULL,
        classification_system VARCHAR NOT NULL,
        valid_from TIMESTAMPTZ,
        valid_to TIMESTAMPTZ,
        source VARCHAR NOT NULL,
        verification_status VARCHAR NOT NULL,
        mapping_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (
            symbol, industry_name, classification_system,
            valid_from, mapping_version
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS universe_sync_runs (
        run_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        provider VARCHAR NOT NULL,
        request_budget BIGINT NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        status VARCHAR NOT NULL,
        expected_count BIGINT NOT NULL,
        received_count BIGINT NOT NULL,
        persisted_count BIGINT NOT NULL,
        conflict_count BIGINT NOT NULL,
        failed_count BIGINT NOT NULL,
        universe_version VARCHAR,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        report_path VARCHAR,
        error_message VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS universe_sync_items (
        run_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        source_record_ids_json JSON NOT NULL,
        differences_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        processed_at TIMESTAMPTZ NOT NULL,
        error_message VARCHAR,
        PRIMARY KEY (run_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_snapshot_runs (
        snapshot_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        provider VARCHAR NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        expected_universe_size BIGINT NOT NULL,
        received_symbol_count BIGINT NOT NULL,
        valid_symbol_count BIGINT NOT NULL,
        missing_symbol_count BIGINT NOT NULL,
        coverage_ratio DOUBLE NOT NULL CHECK (
            coverage_ratio >= 0 AND coverage_ratio <= 1
        ),
        completeness_status VARCHAR NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        snapshot_time TIMESTAMPTZ NOT NULL,
        content_hash VARCHAR NOT NULL,
        request_count BIGINT NOT NULL,
        elapsed_seconds DOUBLE NOT NULL,
        peak_memory_bytes BIGINT,
        database_growth_bytes BIGINT,
        error_message VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS market_snapshot_items (
        snapshot_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        price DOUBLE,
        previous_close DOUBLE,
        open DOUBLE,
        high DOUBLE,
        low DOUBLE,
        volume DOUBLE,
        amount DOUBLE,
        change DOUBLE,
        change_pct DOUBLE,
        turnover_rate DOUBLE,
        snapshot_time TIMESTAMPTZ NOT NULL,
        source VARCHAR NOT NULL,
        item_status VARCHAR NOT NULL,
        is_suspended BOOLEAN NOT NULL,
        is_abnormal BOOLEAN NOT NULL,
        raw_record_id VARCHAR,
        payload_json JSON NOT NULL,
        PRIMARY KEY (snapshot_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS data_expansion_runs (
        run_id VARCHAR PRIMARY KEY,
        expansion_type VARCHAR NOT NULL,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        analysis_mode VARCHAR NOT NULL,
        provider VARCHAR NOT NULL,
        request_budget BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        filters_json JSON NOT NULL,
        status VARCHAR NOT NULL,
        processed_count BIGINT NOT NULL,
        success_count BIGINT NOT NULL,
        skipped_count BIGINT NOT NULL,
        conflict_count BIGINT NOT NULL,
        failed_count BIGINT NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        report_path VARCHAR,
        error_message VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS data_expansion_items (
        run_id VARCHAR NOT NULL,
        item_key VARCHAR NOT NULL,
        symbol VARCHAR,
        data_type VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        source_record_ids_json JSON NOT NULL,
        canonical_record_ids_json JSON NOT NULL,
        event_cluster_ids_json JSON NOT NULL,
        request_count BIGINT NOT NULL,
        processed_at TIMESTAMPTZ NOT NULL,
        error_type VARCHAR,
        error_message VARCHAR,
        PRIMARY KEY (run_id, item_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS entity_link_audits (
        audit_id VARCHAR PRIMARY KEY,
        event_cluster_id VARCHAR NOT NULL,
        symbol VARCHAR,
        link_type VARCHAR NOT NULL,
        matched_text VARCHAR,
        relevance_weight DOUBLE NOT NULL CHECK (
            relevance_weight >= 0 AND relevance_weight <= 1
        ),
        confidence DOUBLE NOT NULL CHECK (
            confidence >= 0 AND confidence <= 1
        ),
        evidence_ids_json JSON NOT NULL,
        matching_rule VARCHAR NOT NULL,
        mapping_version VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        manually_confirmed BOOLEAN NOT NULL,
        status VARCHAR NOT NULL,
        supersedes_audit_id VARCHAR,
        payload_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS data_coverage_snapshots (
        coverage_id VARCHAR PRIMARY KEY,
        data_cutoff TIMESTAMPTZ NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        universe_version VARCHAR,
        payload_json JSON NOT NULL,
        content_hash VARCHAR NOT NULL UNIQUE,
        report_path VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_enrichment_runs (
        run_id VARCHAR PRIMARY KEY,
        mode VARCHAR NOT NULL CHECK (mode IN ('DRY_RUN', 'APPLY')),
        analysis_mode VARCHAR NOT NULL,
        provider VARCHAR NOT NULL,
        request_budget BIGINT NOT NULL,
        request_count BIGINT NOT NULL,
        model_call_budget BIGINT NOT NULL,
        model_call_count BIGINT NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        candidate_count BIGINT NOT NULL,
        status VARCHAR NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        error_message VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS candidate_enrichment_items (
        run_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        enrichment_status VARCHAR NOT NULL,
        fetched_data_types_json JSON NOT NULL,
        missing_data_types_json JSON NOT NULL,
        elapsed_time DOUBLE NOT NULL,
        risk_flags_json JSON NOT NULL,
        request_count BIGINT NOT NULL,
        model_call_count BIGINT NOT NULL,
        error_message VARCHAR,
        PRIMARY KEY (run_id, symbol)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_provider_capability
    ON provider_capabilities (provider, capability, verified_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_stock_universe_symbol_status
    ON stock_universe (symbol, listing_status, universe_version)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_stock_universe_available
    ON stock_universe (data_available_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_stock_alias_normalized
    ON stock_aliases (normalized_alias, valid_from, valid_to)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_industry_symbol
    ON stock_industry_memberships (symbol, valid_from, valid_to)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_universe_run_status
    ON universe_sync_runs (status, started_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_universe_item_run
    ON universe_sync_items (run_id, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_market_snapshot_time
    ON market_snapshot_runs (snapshot_time, completeness_status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_market_snapshot_symbol
    ON market_snapshot_items (symbol, snapshot_time)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_expansion_run_status
    ON data_expansion_runs (run_id, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_expansion_item_symbol
    ON data_expansion_items (symbol, data_type, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_entity_link_event
    ON entity_link_audits (event_cluster_id, generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_entity_link_symbol
    ON entity_link_audits (symbol, generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_coverage_cutoff
    ON data_coverage_snapshots (data_cutoff)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enrichment_run
    ON candidate_enrichment_runs (run_id, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_enrichment_symbol
    ON candidate_enrichment_items (symbol, enrichment_status)
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
