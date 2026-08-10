from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from database.db import get_connection, initialize_database
from trading.research.fundamental.models import (
    FundamentalAnalysisResult,
    MarketValuationPoint,
    PointInTimeFinancialRecord,
)
from trading.schemas import AnalysisMode, FundamentalSnapshot


def _json_load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class FundamentalRepository:
    """Point-in-time reads plus append-only fundamental audit writes."""

    def __init__(self) -> None:
        initialize_database()

    @staticmethod
    def _financial_record(row: tuple[Any, ...]) -> PointInTimeFinancialRecord:
        source_record_ids = list(_json_load(row[9]))
        return PointInTimeFinancialRecord(
            canonical_record_id=row[0],
            symbol=row[1],
            data_type=row[2],
            report_period=row[3],
            announcement_time=row[4],
            data_available_time=row[5],
            event_time=row[6],
            fetched_at=None,
            primary_source=row[7],
            verification_status=row[8],
            source_record_ids=source_record_ids,
            confidence=row[10],
            statement_type=row[11],
            statement_version=row[12],
            revision_of_record_id=row[13],
            accounting_scope=row[14],
            period_type=row[15],
            source_type=row[16],
            disclosure_time_source=row[17],
            payload=_json_load(row[18]),
        )

    def list_at_cutoff(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
        allow_missing_disclosure: bool,
        include_conflicts: bool,
    ) -> list[PointInTimeFinancialRecord]:
        clauses = [
            "symbol = ?",
            """
            (
                data_available_time <= ?
                OR (
                    ? = TRUE
                    AND data_available_time IS NULL
                    AND event_time <= ?
                )
            )
            """,
        ]
        parameters: list[Any] = [
            symbol,
            data_cutoff,
            allow_missing_disclosure,
            data_cutoff,
        ]
        if not include_conflicts:
            clauses.append("verification_status <> 'CONFLICT'")
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT canonical_record_id, symbol, data_type,
                       report_period, announcement_time,
                       data_available_time, event_time, primary_source,
                       verification_status, source_record_ids_json,
                       confidence, statement_type, statement_version,
                       revision_of_record_id, accounting_scope,
                       period_type, source_type, disclosure_time_source,
                       payload_json
                FROM canonical_financial_records
                WHERE {' AND '.join(clauses)}
                ORDER BY report_period DESC NULLS LAST,
                         statement_type,
                         data_available_time DESC NULLS LAST,
                         canonical_record_id
                """,
                parameters,
            ).fetchall()
        return [self._financial_record(row) for row in rows]

    def list_screening_records(
        self,
        *,
        symbols: list[str],
        data_cutoff: datetime,
    ) -> list[PointInTimeFinancialRecord]:
        unique_symbols = list(dict.fromkeys(symbols))
        if not unique_symbols:
            return []
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT canonical_record_id, symbol, data_type,
                       report_period, announcement_time,
                       data_available_time, event_time, primary_source,
                       verification_status, source_record_ids_json,
                       confidence, statement_type, statement_version,
                       revision_of_record_id, accounting_scope,
                       period_type, source_type, disclosure_time_source,
                       payload_json
                FROM canonical_financial_records
                WHERE symbol IN (
                    SELECT UNNEST(?::VARCHAR[])
                )
                  AND (
                      data_available_time <= ?
                      OR (
                          data_available_time IS NULL
                          AND event_time <= ?
                      )
                  )
                ORDER BY symbol, report_period DESC NULLS LAST,
                         statement_type, canonical_record_id
                """,
                [unique_symbols, data_cutoff, data_cutoff],
            ).fetchall()
        return [self._financial_record(row) for row in rows]

    def conflicts(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
    ) -> list[PointInTimeFinancialRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT canonical_record_id, symbol, data_type,
                       report_period, announcement_time,
                       data_available_time, event_time, primary_source,
                       verification_status, source_record_ids_json,
                       confidence, statement_type, statement_version,
                       revision_of_record_id, accounting_scope,
                       period_type, source_type, disclosure_time_source,
                       payload_json
                FROM canonical_financial_records
                WHERE symbol = ?
                  AND verification_status = 'CONFLICT'
                  AND (
                      data_available_time <= ?
                      OR (
                          data_available_time IS NULL
                          AND event_time <= ?
                      )
                  )
                ORDER BY report_period DESC NULLS LAST,
                         canonical_record_id
                """,
                [symbol, data_cutoff, data_cutoff],
            ).fetchall()
        return [self._financial_record(row) for row in rows]

    def latest_market_point(
        self,
        *,
        symbol: str,
        data_cutoff: datetime,
    ) -> MarketValuationPoint | None:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT canonical_record_id, source_record_ids_json,
                       event_time, payload_json
                FROM canonical_market_records
                WHERE symbol = ?
                  AND event_time <= ?
                  AND verification_status <> 'CONFLICT'
                ORDER BY event_time DESC, canonical_record_id
                """,
                [symbol, data_cutoff],
            ).fetchall()
        for row in rows:
            payload = _json_load(row[3])
            close = payload.get("close") if isinstance(payload, dict) else None
            if isinstance(close, bool) or not isinstance(close, (int, float)):
                continue
            if close <= 0:
                continue
            return MarketValuationPoint(
                canonical_record_id=row[0],
                source_record_ids=list(_json_load(row[1])),
                event_time=row[2],
                close=float(close),
            )
        return None

    def record_manual_input(
        self,
        *,
        symbol: str,
        snapshot: FundamentalSnapshot,
        analysis_mode: AnalysisMode,
        override_requested: bool,
        override_reason: str | None,
        operator_confirmed: bool,
        accepted_for_analysis: bool,
        provided_at: datetime,
    ) -> str:
        manual_input_id = "mfi_" + uuid4().hex
        payload = snapshot.model_dump(mode="json", exclude_none=True)
        provided_fields = sorted(
            field
            for field, value in payload.items()
            if value not in (None, [], {})
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO manual_fundamental_inputs (
                    manual_input_id, symbol, source_type, provided_at,
                    verification_status, provided_fields_json,
                    payload_json, override_requested, override_reason,
                    operator_confirmed, analysis_mode,
                    accepted_for_analysis, created_at
                )
                VALUES (
                    ?, ?, 'USER_PROVIDED', ?, 'UNVERIFIED',
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    manual_input_id,
                    symbol,
                    provided_at,
                    _json_dump(provided_fields),
                    _json_dump(payload),
                    override_requested,
                    override_reason,
                    operator_confirmed,
                    analysis_mode.value,
                    accepted_for_analysis,
                    provided_at,
                ],
            )
        return manual_input_id

    def record_analysis(self, result: FundamentalAnalysisResult) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO fundamental_analysis_audits (
                    audit_id, symbol, analysis_mode, data_cutoff,
                    status, used_report_period, announcement_time,
                    data_available_time, verification_status,
                    canonical_record_ids_json, source_record_ids_json,
                    valuation_evidence_ids_json, manual_input_id,
                    missing_fields_json, risk_flags_json, metrics_json,
                    auto_fetch_attempted, auto_fetch_result,
                    algorithm_version, input_snapshot_hash, created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (audit_id) DO NOTHING
                """,
                [
                    result.audit_id,
                    result.symbol,
                    result.analysis_mode.value,
                    result.data_cutoff,
                    result.status.value,
                    result.used_report_period,
                    result.announcement_time,
                    result.data_available_time,
                    result.verification_status,
                    _json_dump(result.canonical_record_ids),
                    _json_dump(result.source_record_ids),
                    _json_dump(result.valuation_evidence_ids),
                    result.manual_input_id,
                    _json_dump(result.missing_fields),
                    _json_dump(
                        [flag.value for flag in result.risk_flags]
                    ),
                    result.metrics.model_dump_json(),
                    result.auto_fetch_attempted,
                    result.auto_fetch_result,
                    result.algorithm_version,
                    result.input_snapshot_hash,
                    datetime.now().astimezone(),
                ],
            )


__all__ = ["FundamentalRepository"]
