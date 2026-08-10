from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb

from config.settings import settings
from database.connection_manager import (
    ManagedDuckDBConnection,
    connect_database,
)
from database.migrations import run_migrations
from data_hub.schemas.market import MarketRecord


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS tasks (
        task_id VARCHAR PRIMARY KEY,
        task_type VARCHAR NOT NULL,
        symbol VARCHAR,
        status VARCHAR NOT NULL,
        request_json JSON,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS data_records (
        record_id VARCHAR PRIMARY KEY,
        task_id VARCHAR,
        symbol VARCHAR NOT NULL,
        data_type VARCHAR NOT NULL,
        event_time TIMESTAMPTZ NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL,
        source_name VARCHAR NOT NULL,
        source_url VARCHAR,
        source_level VARCHAR NOT NULL,
        verified BOOLEAN NOT NULL,
        content_hash VARCHAR,
        payload_json JSON NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS evidence (
        evidence_id VARCHAR PRIMARY KEY,
        task_id VARCHAR,
        record_id VARCHAR,
        source_name VARCHAR NOT NULL,
        source_url VARCHAR,
        source_level VARCHAR NOT NULL,
        event_time TIMESTAMPTZ,
        fetched_at TIMESTAMPTZ NOT NULL,
        content_hash VARCHAR,
        verified BOOLEAN NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS model_calls (
        call_id VARCHAR PRIMARY KEY,
        task_id VARCHAR,
        agent_role VARCHAR NOT NULL,
        provider VARCHAR NOT NULL,
        model VARCHAR NOT NULL,
        input_tokens BIGINT,
        output_tokens BIGINT,
        latency_ms BIGINT,
        estimated_cost DOUBLE,
        success BOOLEAN NOT NULL,
        error_type VARCHAR,
        error_message VARCHAR,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_results (
        result_id VARCHAR PRIMARY KEY,
        task_id VARCHAR NOT NULL,
        agent_role VARCHAR NOT NULL,
        provider VARCHAR,
        model VARCHAR,
        confidence DOUBLE,
        success BOOLEAN NOT NULL,
        result_json JSON,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS human_reviews (
        review_id VARCHAR PRIMARY KEY,
        task_id VARCHAR NOT NULL,
        reviewer VARCHAR,
        score DOUBLE,
        modified BOOLEAN NOT NULL,
        notes VARCHAR,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_traces (
        trace_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        as_of_date DATE NOT NULL,
        final_action VARCHAR NOT NULL,
        vetoed BOOLEAN NOT NULL,
        trace_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_packets (
        decision_id VARCHAR NOT NULL,
        decision_version BIGINT NOT NULL,
        supersedes_version BIGINT,
        symbol VARCHAR NOT NULL,
        generated_at TIMESTAMPTZ NOT NULL,
        data_cutoff_time TIMESTAMPTZ NOT NULL,
        packet_hash VARCHAR NOT NULL,
        schema_version VARCHAR NOT NULL,
        generator_version VARCHAR NOT NULL,
        status VARCHAR NOT NULL CHECK (status IN ('DRAFT', 'FINAL', 'SUPERSEDED')),
        payload_json JSON NOT NULL,
        source_record_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (decision_id, decision_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_evidence (
        decision_id VARCHAR NOT NULL,
        decision_version BIGINT NOT NULL,
        source_record_id VARCHAR NOT NULL,
        verified_snapshot BOOLEAN NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (decision_id, decision_version, source_record_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_opinions (
        decision_id VARCHAR NOT NULL,
        decision_version BIGINT NOT NULL,
        opinion_index BIGINT NOT NULL,
        agent_role VARCHAR NOT NULL,
        opinion_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (decision_id, decision_version, opinion_index)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS risk_vetoes (
        decision_id VARCHAR NOT NULL,
        decision_version BIGINT NOT NULL,
        vetoed BOOLEAN NOT NULL,
        risk_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (decision_id, decision_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS adversarial_reviews (
        review_id VARCHAR PRIMARY KEY,
        decision_id VARCHAR NOT NULL,
        decision_version BIGINT NOT NULL,
        review_kind VARCHAR NOT NULL CHECK (review_kind IN ('INITIAL', 'CHALLENGE')),
        challenge_text VARCHAR,
        review_json JSON NOT NULL,
        data_cutoff_time TIMESTAMPTZ NOT NULL,
        source_record_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        created_version BIGINT,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_integrity_audit (
        audit_id VARCHAR PRIMARY KEY,
        decision_id VARCHAR NOT NULL,
        decision_version BIGINT NOT NULL,
        event_type VARCHAR NOT NULL,
        details VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_trade_previews (
        confirmation_id VARCHAR PRIMARY KEY,
        requested_by VARCHAR NOT NULL,
        channel VARCHAR NOT NULL,
        payload_json JSON NOT NULL,
        payload_hash VARCHAR NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        status VARCHAR NOT NULL
            CHECK (status IN ('PENDING', 'CONFIRMED', 'EXPIRED', 'CANCELLED')),
        created_at TIMESTAMPTZ NOT NULL,
        confirmed_at TIMESTAMPTZ
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_trades (
        trade_id VARCHAR PRIMARY KEY,
        portfolio_id VARCHAR NOT NULL,
        client_trade_id VARCHAR NOT NULL,
        symbol VARCHAR NOT NULL,
        side VARCHAR NOT NULL CHECK (side IN ('BUY', 'SELL')),
        quantity BIGINT NOT NULL CHECK (quantity > 0),
        price DOUBLE NOT NULL CHECK (price > 0),
        fees DOUBLE NOT NULL,
        taxes DOUBLE NOT NULL,
        traded_at TIMESTAMPTZ NOT NULL,
        decision_id VARCHAR,
        source VARCHAR NOT NULL
            CHECK (source IN ('USER_REPORTED', 'USER_IMPORTED')),
        verification_status VARCHAR NOT NULL
            DEFAULT 'USER_REPORTED'
            CHECK (verification_status = 'USER_REPORTED'),
        user_confirmed BOOLEAN NOT NULL CHECK (user_confirmed = TRUE),
        created_at TIMESTAMPTZ NOT NULL,
        created_by VARCHAR NOT NULL,
        correction_of_trade_id VARCHAR,
        notes VARCHAR,
        UNIQUE (portfolio_id, client_trade_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_trade_corrections (
        correction_id VARCHAR PRIMARY KEY,
        original_trade_id VARCHAR NOT NULL,
        correction_trade_id VARCHAR NOT NULL UNIQUE,
        correction_type VARCHAR NOT NULL
            CHECK (correction_type IN ('CORRECTION', 'REVERSAL')),
        reason VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        created_by VARCHAR NOT NULL,
        UNIQUE (original_trade_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_trade_reviews (
        review_id VARCHAR PRIMARY KEY,
        confirmation_id VARCHAR,
        trade_id VARCHAR,
        review_type VARCHAR NOT NULL
            CHECK (review_type IN ('CONFIRMATION', 'RISK_REVIEW')),
        review_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        created_by VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_position_risk_reviews (
        review_id VARCHAR PRIMARY KEY,
        position_id VARCHAR NOT NULL,
        reviewed_at TIMESTAMPTZ NOT NULL,
        data_cutoff_time TIMESTAMPTZ NOT NULL,
        risk_level VARCHAR NOT NULL
            CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
        original_thesis_status VARCHAR NOT NULL
            CHECK (
                original_thesis_status IN (
                    'CONSISTENT', 'WEAKENED', 'INVALIDATED', 'UNKNOWN'
                )
            ),
        recommended_action VARCHAR NOT NULL
            CHECK (
                recommended_action IN (
                    'CONTINUE_OBSERVATION',
                    'HUMAN_REVIEW_REQUIRED',
                    'CONSIDER_REDUCING',
                    'CONSIDER_EXITING'
                )
            ),
        evidence_record_ids_json JSON NOT NULL,
        model_call_ids_json JSON NOT NULL,
        review_json JSON NOT NULL,
        created_by VARCHAR NOT NULL,
        channel VARCHAR NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS trading_orders (
        order_id VARCHAR PRIMARY KEY,
        client_order_id VARCHAR UNIQUE NOT NULL,
        symbol VARCHAR NOT NULL,
        side VARCHAR NOT NULL,
        quantity BIGINT NOT NULL,
        mode VARCHAR NOT NULL,
        status VARCHAR NOT NULL,
        fill_price DOUBLE,
        fee DOUBLE NOT NULL,
        reason VARCHAR,
        order_json JSON NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS paper_accounts (
        account_id VARCHAR PRIMARY KEY,
        account_json JSON NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL
    )
    """,
]


def get_connection(
    *,
    read_only: bool = False,
) -> ManagedDuckDBConnection:
    database_path = Path(settings.opc_database_path)
    owner_process = os.environ.get("HERMES_DB_OWNER_PROCESS") == "router"
    return connect_database(
        database_path,
        configured_path=database_path,
        read_only=(read_only and not owner_process),
    )


def open_database(
    database_path: str | Path,
    *,
    read_only: bool = False,
) -> ManagedDuckDBConnection:
    configured_path = Path(settings.opc_database_path)
    path = Path(database_path)
    owner_process = os.environ.get("HERMES_DB_OWNER_PROCESS") == "router"
    is_main = path.resolve() == configured_path.resolve()
    return connect_database(
        path,
        configured_path=configured_path,
        read_only=(read_only and not (owner_process and is_main)),
    )

def initialize_database() -> None:
    with get_connection() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)
        run_migrations(connection)


def insert_market_record(
    connection: duckdb.DuckDBPyConnection,
    record: MarketRecord,
    task_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO data_records (
            record_id,
            task_id,
            symbol,
            data_type,
            event_time,
            fetched_at,
            source_name,
            source_url,
            source_level,
            verified,
            content_hash,
            payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            record.record_id,
            task_id,
            record.symbol,
            record.data_type.value,
            record.event_time,
            record.fetched_at,
            record.source_name,
            str(record.source_url) if record.source_url else None,
            record.source_level.value,
            record.verified,
            record.content_hash,
            json.dumps(record.data, ensure_ascii=False, default=str),
        ],
    )
