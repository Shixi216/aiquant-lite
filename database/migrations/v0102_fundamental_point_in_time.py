from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import duckdb


MIGRATION_ID = "0102_fundamental_point_in_time"
NORMALIZATION_VERSION = "fundamental-point-in-time-v1"
DISCLOSURE_DATE_AVAILABLE_HOUR = 18
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

MIGRATION_STATEMENTS = (
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS report_period VARCHAR
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS announcement_time TIMESTAMPTZ
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS data_available_time TIMESTAMPTZ
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS statement_type VARCHAR
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS statement_version VARCHAR
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS revision_of_record_id VARCHAR
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS accounting_scope VARCHAR
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS period_type VARCHAR
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS source_type VARCHAR
    """,
    """
    ALTER TABLE canonical_financial_records
    ADD COLUMN IF NOT EXISTS disclosure_time_source VARCHAR
    """,
    """
    CREATE TABLE IF NOT EXISTS manual_fundamental_inputs (
        manual_input_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        source_type VARCHAR NOT NULL
            CHECK (source_type = 'USER_PROVIDED'),
        provided_at TIMESTAMPTZ NOT NULL,
        verification_status VARCHAR NOT NULL,
        provided_fields_json JSON NOT NULL,
        payload_json JSON NOT NULL,
        override_requested BOOLEAN NOT NULL,
        override_reason VARCHAR,
        operator_confirmed BOOLEAN NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (
                analysis_mode IN (
                    'SCREENING', 'RESEARCH', 'DECISION'
                )
            ),
        accepted_for_analysis BOOLEAN NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fundamental_analysis_audits (
        audit_id VARCHAR PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        analysis_mode VARCHAR NOT NULL
            CHECK (
                analysis_mode IN (
                    'SCREENING', 'RESEARCH', 'DECISION'
                )
            ),
        data_cutoff TIMESTAMPTZ NOT NULL,
        status VARCHAR NOT NULL,
        used_report_period VARCHAR,
        announcement_time TIMESTAMPTZ,
        data_available_time TIMESTAMPTZ,
        verification_status VARCHAR,
        canonical_record_ids_json JSON NOT NULL,
        source_record_ids_json JSON NOT NULL,
        valuation_evidence_ids_json JSON NOT NULL,
        manual_input_id VARCHAR,
        missing_fields_json JSON NOT NULL,
        risk_flags_json JSON NOT NULL,
        metrics_json JSON NOT NULL,
        auto_fetch_attempted BOOLEAN NOT NULL,
        auto_fetch_result VARCHAR,
        algorithm_version VARCHAR NOT NULL,
        input_snapshot_hash VARCHAR NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_canonical_financial_point_in_time
    ON canonical_financial_records (
        symbol, data_available_time, report_period, statement_type
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_manual_fundamental_symbol_time
    ON manual_fundamental_inputs (symbol, provided_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_fundamental_audit_symbol_cutoff
    ON fundamental_analysis_audits (
        symbol, analysis_mode, data_cutoff
    )
    """,
)


def _checksum() -> str:
    payload = "\n".join(statement.strip() for statement in MIGRATION_STATEMENTS)
    payload += f"\n{NORMALIZATION_VERSION}:{DISCLOSURE_DATE_AVAILABLE_HOUR}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _announcement_time(payload: dict[str, Any]) -> tuple[datetime | None, str | None]:
    for field in ("f_ann_date", "ann_date", "announcement_date"):
        value = _text(payload.get(field))
        if value is None:
            continue
        normalized = value.replace("-", "")
        if len(normalized) != 8 or not normalized.isdigit():
            continue
        try:
            parsed = datetime.strptime(normalized, "%Y%m%d")
        except ValueError:
            continue
        return (
            parsed.replace(
                hour=DISCLOSURE_DATE_AVAILABLE_HOUR,
                tzinfo=SHANGHAI_TZ,
            ),
            field,
        )
    return None, None


def _period_type(payload: dict[str, Any]) -> str | None:
    end_type = _text(payload.get("end_type"))
    mapped = {
        "1": "QUARTERLY",
        "2": "SEMIANNUAL",
        "3": "QUARTERLY",
        "4": "ANNUAL",
    }.get(end_type or "")
    if mapped is not None:
        return mapped
    report_period = _text(
        payload.get("report_period") or payload.get("end_date")
    )
    if report_period is None:
        return None
    normalized = report_period.replace("-", "")
    if normalized.endswith("0331") or normalized.endswith("0930"):
        return "QUARTERLY"
    if normalized.endswith("0630"):
        return "SEMIANNUAL"
    if normalized.endswith("1231"):
        return "ANNUAL"
    return None


def _normalize_existing_rows(connection: duckdb.DuckDBPyConnection) -> None:
    rows = connection.execute(
        """
        SELECT canonical_record_id, payload_json
        FROM canonical_financial_records
        """
    ).fetchall()
    for canonical_record_id, raw_payload in rows:
        payload = _payload(raw_payload)
        announcement_time, time_source = _announcement_time(payload)
        connection.execute(
            """
            UPDATE canonical_financial_records
            SET report_period = COALESCE(report_period, ?),
                announcement_time = COALESCE(announcement_time, ?),
                data_available_time = COALESCE(data_available_time, ?),
                statement_type = COALESCE(statement_type, ?),
                statement_version = COALESCE(statement_version, ?),
                accounting_scope = COALESCE(accounting_scope, ?),
                period_type = COALESCE(period_type, ?),
                source_type = COALESCE(source_type, 'CANONICAL'),
                disclosure_time_source = COALESCE(
                    disclosure_time_source, ?
                )
            WHERE canonical_record_id = ?
            """,
            [
                _text(
                    payload.get("report_period") or payload.get("end_date")
                ),
                announcement_time,
                announcement_time,
                _text(payload.get("statement_type")),
                _text(payload.get("update_flag")),
                _text(payload.get("comp_type")),
                _period_type(payload),
                time_source,
                canonical_record_id,
            ],
        )


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
        _normalize_existing_rows(connection)
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
    "DISCLOSURE_DATE_AVAILABLE_HOUR",
    "MIGRATION_ID",
    "MIGRATION_STATEMENTS",
    "NORMALIZATION_VERSION",
    "apply_migration",
]
