from __future__ import annotations

import json
from pathlib import Path

import duckdb

from config.settings import settings
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
]


def get_connection() -> duckdb.DuckDBPyConnection:
    database_path = Path(settings.opc_database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(database_path))


def initialize_database() -> None:
    with get_connection() as connection:
        for statement in SCHEMA_STATEMENTS:
            connection.execute(statement)


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