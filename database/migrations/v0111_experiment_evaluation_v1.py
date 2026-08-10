from __future__ import annotations

import hashlib
from datetime import datetime

import duckdb


MIGRATION_ID = "0111_experiment_evaluation_v1"
SCHEMA_VERSION = "experiment-evaluation-v1"

MIGRATION_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS experiment_definitions (
        experiment_id VARCHAR PRIMARY KEY,
        experiment_name VARCHAR NOT NULL,
        experiment_type VARCHAR NOT NULL,
        description VARCHAR NOT NULL,
        hypothesis VARCHAR NOT NULL,
        universe_definition JSON NOT NULL,
        query_plan_id VARCHAR,
        scanner_version VARCHAR,
        orchestration_version VARCHAR,
        formal_strategy_version VARCHAR NOT NULL,
        shadow_strategy_version VARCHAR NOT NULL,
        signal_frequency VARCHAR NOT NULL,
        signal_time_policy VARCHAR NOT NULL,
        entry_policy VARCHAR NOT NULL,
        exit_policy VARCHAR NOT NULL,
        holding_periods_json JSON NOT NULL,
        top_k BIGINT NOT NULL CHECK (top_k > 0),
        weighting_method VARCHAR NOT NULL,
        benchmark_definition VARCHAR NOT NULL,
        cost_model JSON,
        missing_data_policy VARCHAR NOT NULL,
        stale_data_policy VARCHAR NOT NULL,
        minimum_sample_size BIGINT NOT NULL CHECK (minimum_sample_size > 0),
        start_trade_date DATE,
        end_trade_date DATE,
        random_seed BIGINT NOT NULL,
        config_json JSON NOT NULL,
        config_hash VARCHAR NOT NULL UNIQUE,
        version BIGINT NOT NULL CHECK (version > 0),
        created_at TIMESTAMPTZ NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        production_weight_update BOOLEAN NOT NULL
            CHECK (production_weight_update = FALSE),
        trade_execution_enabled BOOLEAN NOT NULL
            CHECK (trade_execution_enabled = FALSE),
        experimental_only BOOLEAN NOT NULL,
        status VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experiment_runs (
        run_id VARCHAR PRIMARY KEY,
        experiment_id VARCHAR NOT NULL,
        run_type VARCHAR NOT NULL,
        started_at TIMESTAMPTZ NOT NULL,
        completed_at TIMESTAMPTZ,
        code_version VARCHAR NOT NULL,
        git_head VARCHAR NOT NULL,
        migration_version VARCHAR NOT NULL,
        database_snapshot_hash VARCHAR NOT NULL,
        dataset_snapshot_hash VARCHAR NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        query_plan_hash VARCHAR NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        as_of_trade_date DATE,
        available_history_start DATE,
        available_history_end DATE,
        eligible_signal_date_count BIGINT NOT NULL,
        generated_signal_count BIGINT NOT NULL,
        labeled_signal_count BIGINT NOT NULL,
        pending_label_count BIGINT NOT NULL,
        invalid_signal_count BIGINT NOT NULL,
        benchmark_available BOOLEAN NOT NULL,
        elapsed_ms DOUBLE NOT NULL,
        peak_memory_bytes BIGINT NOT NULL,
        database_query_count BIGINT NOT NULL,
        network_request_count BIGINT NOT NULL CHECK (network_request_count = 0),
        model_call_count BIGINT NOT NULL CHECK (model_call_count = 0),
        status VARCHAR NOT NULL,
        risk_flags_json JSON NOT NULL,
        data_quality_summary_json JSON NOT NULL,
        sanitized_error VARCHAR,
        payload_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        profitability_proven BOOLEAN NOT NULL CHECK (profitability_proven = FALSE)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experiment_observations (
        observation_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        signal_id VARCHAR NOT NULL,
        signal_type VARCHAR NOT NULL,
        signal_trade_date DATE NOT NULL,
        signal_generated_at TIMESTAMPTZ NOT NULL,
        data_cutoff TIMESTAMPTZ NOT NULL,
        actionable_from_trade_date DATE,
        symbol VARCHAR NOT NULL,
        rank BIGINT NOT NULL CHECK (rank > 0),
        scanner_score DOUBLE,
        technical_score DOUBLE,
        fundamental_score DOUBLE,
        formal_score DOUBLE,
        formal_action VARCHAR,
        shadow_score DOUBLE,
        composite_confidence DOUBLE,
        factor_coverage VARCHAR NOT NULL,
        available_factors_json JSON NOT NULL,
        missing_factors_json JSON NOT NULL,
        anomaly_types_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        veto_status VARCHAR NOT NULL,
        query_plan_hash VARCHAR NOT NULL,
        signal_snapshot_hash VARCHAR NOT NULL,
        source_run_id VARCHAR,
        stale_snapshot BOOLEAN NOT NULL,
        point_in_time_valid BOOLEAN NOT NULL,
        payload_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE (run_id, signal_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS forward_return_labels (
        label_id VARCHAR PRIMARY KEY,
        observation_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        signal_trade_date DATE NOT NULL,
        entry_trade_date DATE,
        exit_trade_date DATE,
        entry_price DOUBLE,
        exit_price DOUBLE,
        entry_price_type VARCHAR NOT NULL,
        exit_price_type VARCHAR NOT NULL,
        gross_return DOUBLE,
        benchmark_return DOUBLE,
        excess_return DOUBLE,
        maximum_favorable_excursion DOUBLE,
        maximum_adverse_excursion DOUBLE,
        was_suspended_on_entry BOOLEAN,
        was_suspended_during_holding BOOLEAN,
        was_price_limit_locked BOOLEAN,
        missing_price_reason VARCHAR,
        return_basis VARCHAR NOT NULL CHECK (return_basis = 'RAW_PRICE_RETURN'),
        horizon_trading_days BIGINT NOT NULL
            CHECK (horizon_trading_days IN (1, 3, 5, 20)),
        label_status VARCHAR NOT NULL,
        risk_flags_json JSON NOT NULL,
        calculated_at TIMESTAMPTZ,
        label_version VARCHAR NOT NULL,
        payload_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (observation_id, horizon_trading_days, label_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_runs (
        backtest_id VARCHAR PRIMARY KEY,
        run_id VARCHAR,
        experiment_id VARCHAR NOT NULL,
        portfolio_method VARCHAR NOT NULL,
        rebalance_method VARCHAR NOT NULL,
        weighting_method VARCHAR NOT NULL,
        horizon_trading_days BIGINT NOT NULL,
        top_k BIGINT NOT NULL,
        max_position_weight DOUBLE NOT NULL,
        cost_model_json JSON,
        gross_return DOUBLE,
        net_return DOUBLE,
        benchmark_return DOUBLE,
        turnover DOUBLE,
        active_days BIGINT NOT NULL,
        status VARCHAR NOT NULL,
        risk_flags_json JSON NOT NULL,
        metrics_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        research_only BOOLEAN NOT NULL CHECK (research_only = TRUE),
        trade_execution_enabled BOOLEAN NOT NULL
            CHECK (trade_execution_enabled = FALSE),
        paper_trading_written BOOLEAN NOT NULL
            CHECK (paper_trading_written = FALSE)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS backtest_positions (
        backtest_id VARCHAR NOT NULL,
        cohort_id VARCHAR NOT NULL,
        signal_trade_date DATE NOT NULL,
        symbol VARCHAR NOT NULL,
        weight DOUBLE NOT NULL CHECK (weight >= 0 AND weight <= 1),
        entry_trade_date DATE,
        exit_trade_date DATE,
        gross_return DOUBLE,
        net_return DOUBLE,
        tradable BOOLEAN NOT NULL,
        risk_flags_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (backtest_id, cohort_id, symbol)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evaluation_metrics (
        metric_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        scope VARCHAR NOT NULL,
        group_name VARCHAR NOT NULL,
        horizon_trading_days BIGINT,
        metric_name VARCHAR NOT NULL,
        metric_value DOUBLE,
        sample_count BIGINT NOT NULL,
        status VARCHAR NOT NULL,
        confidence_interval_json JSON,
        risk_flags_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (
            run_id, scope, group_name, horizon_trading_days, metric_name
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evaluation_slices (
        slice_id VARCHAR PRIMARY KEY,
        run_id VARCHAR NOT NULL,
        dimension VARCHAR NOT NULL,
        slice_value VARCHAR NOT NULL,
        horizon_trading_days BIGINT,
        sample_count BIGINT NOT NULL,
        minimum_sample_size BIGINT NOT NULL,
        conclusion_available BOOLEAN NOT NULL,
        metrics_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        UNIQUE (run_id, dimension, slice_value, horizon_trading_days)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS experiment_data_quality_audits (
        audit_id VARCHAR PRIMARY KEY,
        experiment_id VARCHAR NOT NULL,
        run_id VARCHAR,
        audit_type VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        sample_count BIGINT NOT NULL,
        details_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_definition_config_hash
    ON experiment_definitions (config_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_run_experiment
    ON experiment_runs (experiment_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_run_dataset
    ON experiment_runs (dataset_snapshot_hash)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_run_generated
    ON experiment_runs (generated_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_observation_run
    ON experiment_observations (run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_observation_symbol_date
    ON experiment_observations (symbol, signal_trade_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_observation_generated
    ON experiment_observations (created_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_forward_label_observation
    ON forward_return_labels (observation_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_forward_label_symbol_date
    ON forward_return_labels (symbol, signal_trade_date)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_forward_label_horizon
    ON forward_return_labels (horizon_trading_days, label_status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_backtest_run_experiment
    ON backtest_runs (experiment_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_metric_run
    ON evaluation_metrics (run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_evaluation_slice_run
    ON evaluation_slices (run_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_experiment_audit_run
    ON experiment_data_quality_audits (run_id)
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    payload += f"\n{SCHEMA_VERSION}"
    return hashlib.sha256(payload.encode()).hexdigest()


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
