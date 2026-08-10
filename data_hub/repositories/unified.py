from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Generic, TypeVar
from zoneinfo import ZoneInfo

from config.settings import settings
from database.db import get_connection, initialize_database
from data_hub.schemas.unified import (
    CanonicalFinancialRecord,
    CanonicalMarketRecord,
    EventCluster,
    FactorOutput,
    VerificationStatus,
)


CanonicalRecordT = TypeVar(
    "CanonicalRecordT",
    CanonicalMarketRecord,
    CanonicalFinancialRecord,
)


class UnifiedRepositoryError(RuntimeError):
    pass


class EvidenceNotFoundError(UnifiedRepositoryError):
    pass


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_load(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _placeholders(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _financial_metadata(
    payload: dict[str, Any],
) -> dict[str, Any]:
    announcement_time: datetime | None = None
    disclosure_source: str | None = None
    for field in ("f_ann_date", "ann_date", "announcement_date"):
        value = payload.get(field)
        if value is None:
            continue
        normalized = str(value).strip().replace("-", "")
        if len(normalized) != 8 or not normalized.isdigit():
            continue
        try:
            announcement_time = datetime.strptime(
                normalized,
                "%Y%m%d",
            ).replace(
                hour=(
                    settings.fundamental_disclosure_date_available_hour
                ),
                tzinfo=ZoneInfo("Asia/Shanghai"),
            )
        except ValueError:
            continue
        disclosure_source = field
        break
    report_period = str(
        payload.get("report_period") or payload.get("end_date") or ""
    ).strip()
    end_type = str(payload.get("end_type") or "").strip()
    period_type = {
        "1": "QUARTERLY",
        "2": "SEMIANNUAL",
        "3": "QUARTERLY",
        "4": "ANNUAL",
    }.get(end_type)
    if period_type is None:
        normalized_period = report_period.replace("-", "")
        if normalized_period.endswith(("0331", "0930")):
            period_type = "QUARTERLY"
        elif normalized_period.endswith("0630"):
            period_type = "SEMIANNUAL"
        elif normalized_period.endswith("1231"):
            period_type = "ANNUAL"
    return {
        "report_period": report_period or None,
        "announcement_time": announcement_time,
        "data_available_time": announcement_time,
        "statement_type": (
            str(payload.get("statement_type")).strip()
            if payload.get("statement_type") is not None
            else None
        ),
        "statement_version": (
            str(payload.get("update_flag")).strip()
            if payload.get("update_flag") is not None
            else None
        ),
        "accounting_scope": (
            str(payload.get("comp_type")).strip()
            if payload.get("comp_type") is not None
            else None
        ),
        "period_type": period_type,
        "source_type": "CANONICAL",
        "disclosure_time_source": disclosure_source,
    }


def _ensure_raw_records_exist(record_ids: list[str]) -> None:
    unique_ids = list(dict.fromkeys(record_ids))
    if not unique_ids:
        raise EvidenceNotFoundError("at least one raw source record is required")
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT record_id
            FROM data_records
            WHERE record_id IN ({_placeholders(unique_ids)})
            """,
            unique_ids,
        ).fetchall()
    found = {row[0] for row in rows}
    missing = [record_id for record_id in unique_ids if record_id not in found]
    if missing:
        raise EvidenceNotFoundError(
            "unknown raw source record IDs: " + ", ".join(missing)
        )


class _CanonicalRepository(Generic[CanonicalRecordT]):
    table_name: str
    model_type: type[CanonicalRecordT]

    def __init__(self) -> None:
        initialize_database()

    def _row_to_record(self, row: tuple[Any, ...]) -> CanonicalRecordT:
        return self.model_type.model_validate(
            {
                "canonical_record_id": row[0],
                "symbol": row[1],
                "data_type": row[2],
                "event_time": row[3],
                "data_cutoff": row[4],
                "generated_at": row[5],
                "primary_source": row[6],
                "source_record_ids": _json_load(row[7]),
                "verification_source_ids": _json_load(row[8]),
                "verification_status": row[9],
                "field_differences": _json_load(row[10]),
                "payload": _json_load(row[11]),
                "confidence": row[12],
                "content_hash": row[13],
                "algorithm_version": row[14],
            }
        )

    def _select_columns(self) -> str:
        return """
            canonical_record_id, symbol, data_type, event_time,
            data_cutoff, generated_at, primary_source,
            source_record_ids_json, verification_source_ids_json,
            verification_status, field_differences_json, payload_json,
            confidence, content_hash, algorithm_version
        """

    def get(self, canonical_record_id: str) -> CanonicalRecordT | None:
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {self._select_columns()}
                FROM {self.table_name}
                WHERE canonical_record_id = ?
                """,
                [canonical_record_id],
            ).fetchone()
        return None if row is None else self._row_to_record(row)

    def get_by_natural_key(
        self,
        symbol: str,
        data_type: str,
        event_time: datetime,
    ) -> CanonicalRecordT | None:
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {self._select_columns()}
                FROM {self.table_name}
                WHERE symbol = ? AND data_type = ? AND event_time = ?
                """,
                [symbol, data_type, event_time],
            ).fetchone()
        return None if row is None else self._row_to_record(row)

    def save(self, record: CanonicalRecordT) -> CanonicalRecordT:
        _ensure_raw_records_exist(record.source_record_ids)
        existing = self.get_by_natural_key(
            record.symbol,
            record.data_type,
            record.event_time,
        )
        if existing is not None and existing.content_hash == record.content_hash:
            return existing

        values = [
            record.canonical_record_id,
            record.symbol,
            record.data_type,
            record.event_time,
            record.data_cutoff,
            record.generated_at,
            record.primary_source,
            _json_dump(record.source_record_ids),
            _json_dump(record.verification_source_ids),
            record.verification_status.value,
            _json_dump(record.field_differences),
            _json_dump(record.payload),
            record.confidence,
            record.content_hash,
            record.algorithm_version,
        ]
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                if existing is None:
                    connection.execute(
                        f"""
                        INSERT INTO {self.table_name} (
                            canonical_record_id, symbol, data_type, event_time,
                            data_cutoff, generated_at, primary_source,
                            source_record_ids_json,
                            verification_source_ids_json,
                            verification_status, field_differences_json,
                            payload_json, confidence, content_hash,
                            algorithm_version
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        values,
                    )
                else:
                    connection.execute(
                        f"""
                        UPDATE {self.table_name}
                        SET data_cutoff = ?, generated_at = ?,
                            primary_source = ?, source_record_ids_json = ?,
                            verification_source_ids_json = ?,
                            verification_status = ?,
                            field_differences_json = ?, payload_json = ?,
                            confidence = ?, content_hash = ?,
                            algorithm_version = ?
                        WHERE canonical_record_id = ?
                        """,
                        [
                            record.data_cutoff,
                            record.generated_at,
                            record.primary_source,
                            _json_dump(record.source_record_ids),
                            _json_dump(record.verification_source_ids),
                            record.verification_status.value,
                            _json_dump(record.field_differences),
                            _json_dump(record.payload),
                            record.confidence,
                            record.content_hash,
                            record.algorithm_version,
                            existing.canonical_record_id,
                        ],
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        persisted_id = (
            existing.canonical_record_id
            if existing is not None
            else record.canonical_record_id
        )
        persisted = self.get(persisted_id)
        if persisted is None:
            raise UnifiedRepositoryError("canonical record was not persisted")
        return persisted

    def list(
        self,
        *,
        symbol: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        data_cutoff: datetime | None = None,
        status: VerificationStatus | None = None,
    ) -> list[CanonicalRecordT]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            parameters.append(symbol)
        if start_time is not None:
            clauses.append("event_time >= ?")
            parameters.append(start_time)
        if end_time is not None:
            clauses.append("event_time <= ?")
            parameters.append(end_time)
        if data_cutoff is not None:
            clauses.append("data_cutoff <= ?")
            parameters.append(data_cutoff)
        if status is not None:
            clauses.append("verification_status = ?")
            parameters.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT {self._select_columns()}
                FROM {self.table_name}
                {where}
                ORDER BY event_time, canonical_record_id
                """,
                parameters,
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def conflicts(self, *, symbol: str | None = None) -> list[CanonicalRecordT]:
        return self.list(symbol=symbol, status=VerificationStatus.CONFLICT)

    def single_source(
        self,
        *,
        symbol: str | None = None,
    ) -> list[CanonicalRecordT]:
        return self.list(symbol=symbol, status=VerificationStatus.SINGLE_SOURCE)

    def resolve_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        record = self.get(evidence_id)
        if record is None:
            return None
        return {
            "layer": self.table_name,
            "record": record.model_dump(mode="json"),
        }


class CanonicalMarketRepository(
    _CanonicalRepository[CanonicalMarketRecord]
):
    table_name = "canonical_market_records"
    model_type = CanonicalMarketRecord


class CanonicalFinancialRepository(
    _CanonicalRepository[CanonicalFinancialRecord]
):
    table_name = "canonical_financial_records"
    model_type = CanonicalFinancialRecord

    def save(
        self,
        record: CanonicalFinancialRecord,
    ) -> CanonicalFinancialRecord:
        persisted = super().save(record)
        metadata = _financial_metadata(persisted.payload)
        with get_connection() as connection:
            previous = connection.execute(
                """
                SELECT canonical_record_id
                FROM canonical_financial_records
                WHERE symbol = ?
                  AND canonical_record_id <> ?
                  AND report_period = ?
                  AND statement_type = ?
                  AND (
                      data_available_time < ?
                      OR (
                          data_available_time = ?
                          AND canonical_record_id < ?
                      )
                  )
                ORDER BY data_available_time DESC NULLS LAST,
                         canonical_record_id DESC
                LIMIT 1
                """,
                [
                    persisted.symbol,
                    persisted.canonical_record_id,
                    metadata["report_period"],
                    metadata["statement_type"],
                    metadata["data_available_time"],
                    metadata["data_available_time"],
                    persisted.canonical_record_id,
                ],
            ).fetchone()
            connection.execute(
                """
                UPDATE canonical_financial_records
                SET report_period = ?,
                    announcement_time = ?,
                    data_available_time = ?,
                    statement_type = ?,
                    statement_version = ?,
                    accounting_scope = ?,
                    period_type = ?,
                    source_type = ?,
                    disclosure_time_source = ?,
                    revision_of_record_id = COALESCE(
                        revision_of_record_id, ?
                    )
                WHERE canonical_record_id = ?
                """,
                [
                    metadata["report_period"],
                    metadata["announcement_time"],
                    metadata["data_available_time"],
                    metadata["statement_type"],
                    metadata["statement_version"],
                    metadata["accounting_scope"],
                    metadata["period_type"],
                    metadata["source_type"],
                    metadata["disclosure_time_source"],
                    previous[0] if previous is not None else None,
                    persisted.canonical_record_id,
                ],
            )
        refreshed = self.get(persisted.canonical_record_id)
        if refreshed is None:
            raise UnifiedRepositoryError(
                "canonical financial record disappeared after metadata update"
            )
        return refreshed


class EventClusterRepository:
    def __init__(self) -> None:
        initialize_database()

    @staticmethod
    def _row_to_cluster(
        row: tuple[Any, ...],
        source_ids: list[str],
        symbols: list[str],
        sectors: list[str],
    ) -> EventCluster:
        return EventCluster(
            event_cluster_id=row[0],
            canonical_title=row[1],
            event_type=row[2],
            event_time=row[3],
            data_cutoff=row[4],
            primary_source_id=row[5],
            source_count=int(row[6]),
            source_record_ids=source_ids,
            symbol_links=symbols,
            sector_links=sectors,
            dedup_method=row[7],
            dedup_version=row[8],
            cluster_hash=row[9],
            generated_at=row[10],
        )

    def get(self, event_cluster_id: str) -> EventCluster | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT event_cluster_id, canonical_title, event_type,
                       event_time, data_cutoff, primary_source_id,
                       source_count, dedup_method, dedup_version,
                       cluster_hash, generated_at
                FROM event_clusters
                WHERE event_cluster_id = ?
                """,
                [event_cluster_id],
            ).fetchone()
            if row is None:
                return None
            source_ids = [
                item[0]
                for item in connection.execute(
                    """
                    SELECT source_record_id
                    FROM event_source_links
                    WHERE event_cluster_id = ?
                    ORDER BY source_record_id
                    """,
                    [event_cluster_id],
                ).fetchall()
            ]
            symbols = [
                item[0]
                for item in connection.execute(
                    """
                    SELECT symbol FROM event_symbol_links
                    WHERE event_cluster_id = ? ORDER BY symbol
                    """,
                    [event_cluster_id],
                ).fetchall()
            ]
            sectors = [
                item[0]
                for item in connection.execute(
                    """
                    SELECT sector FROM event_sector_links
                    WHERE event_cluster_id = ? ORDER BY sector
                    """,
                    [event_cluster_id],
                ).fetchall()
            ]
        return self._row_to_cluster(row, source_ids, symbols, sectors)

    def get_by_source_id(self, source_record_id: str) -> EventCluster | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT event_cluster_id
                FROM event_source_links
                WHERE source_record_id = ?
                """,
                [source_record_id],
            ).fetchone()
        return None if row is None else self.get(row[0])

    def candidates(
        self,
        *,
        event_type: str,
        event_time: datetime,
        window: timedelta,
    ) -> list[EventCluster]:
        with get_connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT event_cluster_id
                    FROM event_clusters
                    WHERE event_type = ?
                      AND event_time BETWEEN ? AND ?
                    ORDER BY event_time, event_cluster_id
                    """,
                    [event_type, event_time - window, event_time + window],
                ).fetchall()
            ]
        return [
            cluster
            for cluster_id in ids
            if (cluster := self.get(cluster_id)) is not None
        ]

    def save(
        self,
        cluster: EventCluster,
        source_levels: dict[str, str],
    ) -> EventCluster:
        _ensure_raw_records_exist(cluster.source_record_ids)
        if set(source_levels) != set(cluster.source_record_ids):
            raise UnifiedRepositoryError(
                "source_levels must cover every event source record"
            )
        existing = self.get(cluster.event_cluster_id)
        if existing is not None and existing.cluster_hash == cluster.cluster_hash:
            return existing

        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                if existing is None:
                    connection.execute(
                        """
                        INSERT INTO event_clusters (
                            event_cluster_id, canonical_title, event_type,
                            event_time, data_cutoff, primary_source_id,
                            source_count, dedup_method, dedup_version,
                            cluster_hash, generated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        [
                            cluster.event_cluster_id,
                            cluster.canonical_title,
                            cluster.event_type,
                            cluster.event_time,
                            cluster.data_cutoff,
                            cluster.primary_source_id,
                            cluster.source_count,
                            cluster.dedup_method,
                            cluster.dedup_version,
                            cluster.cluster_hash,
                            cluster.generated_at,
                        ],
                    )
                else:
                    connection.execute(
                        """
                        UPDATE event_clusters
                        SET canonical_title = ?, event_time = ?,
                            data_cutoff = ?, primary_source_id = ?,
                            source_count = ?, dedup_method = ?,
                            dedup_version = ?, cluster_hash = ?,
                            generated_at = ?
                        WHERE event_cluster_id = ?
                        """,
                        [
                            cluster.canonical_title,
                            cluster.event_time,
                            cluster.data_cutoff,
                            cluster.primary_source_id,
                            cluster.source_count,
                            cluster.dedup_method,
                            cluster.dedup_version,
                            cluster.cluster_hash,
                            cluster.generated_at,
                            cluster.event_cluster_id,
                        ],
                    )
                    connection.execute(
                        """
                        UPDATE event_source_links
                        SET is_primary = FALSE
                        WHERE event_cluster_id = ?
                        """,
                        [cluster.event_cluster_id],
                    )

                for source_id in cluster.source_record_ids:
                    connection.execute(
                        """
                        INSERT INTO event_source_links (
                            event_cluster_id, source_record_id,
                            source_level, is_primary, linked_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (source_record_id) DO UPDATE SET
                            is_primary = EXCLUDED.is_primary,
                            source_level = EXCLUDED.source_level
                        """,
                        [
                            cluster.event_cluster_id,
                            source_id,
                            source_levels[source_id],
                            source_id == cluster.primary_source_id,
                            cluster.generated_at,
                        ],
                    )
                for symbol in cluster.symbol_links:
                    connection.execute(
                        """
                        INSERT INTO event_symbol_links (
                            event_cluster_id, symbol
                        )
                        VALUES (?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [cluster.event_cluster_id, symbol],
                    )
                for sector in cluster.sector_links:
                    connection.execute(
                        """
                        INSERT INTO event_sector_links (
                            event_cluster_id, sector
                        )
                        VALUES (?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        [cluster.event_cluster_id, sector],
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        persisted = self.get(cluster.event_cluster_id)
        if persisted is None:
            raise UnifiedRepositoryError("event cluster was not persisted")
        return persisted

    def list(
        self,
        *,
        symbol: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        data_cutoff: datetime | None = None,
    ) -> list[EventCluster]:
        joins = ""
        clauses: list[str] = []
        parameters: list[Any] = []
        if symbol is not None:
            joins = (
                "JOIN event_symbol_links l "
                "ON l.event_cluster_id = c.event_cluster_id"
            )
            clauses.append("l.symbol = ?")
            parameters.append(symbol)
        if start_time is not None:
            clauses.append("c.event_time >= ?")
            parameters.append(start_time)
        if end_time is not None:
            clauses.append("c.event_time <= ?")
            parameters.append(end_time)
        if data_cutoff is not None:
            clauses.append("c.data_cutoff <= ?")
            parameters.append(data_cutoff)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_connection() as connection:
            ids = [
                row[0]
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT c.event_cluster_id
                    FROM event_clusters c
                    {joins}
                    {where}
                    ORDER BY c.event_cluster_id
                    """,
                    parameters,
                ).fetchall()
            ]
        return [
            cluster
            for cluster_id in ids
            if (cluster := self.get(cluster_id)) is not None
        ]

    def resolve_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        cluster = self.get(evidence_id)
        if cluster is not None:
            return {
                "layer": "event_clusters",
                "record": cluster.model_dump(mode="json"),
            }
        return None


_FACTOR_EVIDENCE_QUERIES = (
    ("data_records", "record_id"),
    ("canonical_market_records", "canonical_record_id"),
    ("canonical_historical_bars", "bar_id"),
    ("canonical_financial_records", "canonical_record_id"),
    ("event_clusters", "event_cluster_id"),
    ("manual_fundamental_inputs", "manual_input_id"),
    ("fundamental_analysis_audits", "audit_id"),
    ("sentiment_event_analyses", "sentiment_analysis_id"),
    ("sentiment_symbol_snapshots", "snapshot_id"),
    ("sentiment_market_snapshots", "market_snapshot_id"),
    ("sentiment_evaluations", "evaluation_id"),
    ("policy_news_event_analyses", "policy_analysis_id"),
    ("policy_news_symbol_snapshots", "snapshot_id"),
    ("policy_news_sector_snapshots", "snapshot_id"),
    ("policy_news_evaluations", "evaluation_id"),
    ("capital_flow_symbol_snapshots", "snapshot_id"),
    ("capital_flow_sector_snapshots", "snapshot_id"),
    ("capital_flow_market_snapshots", "snapshot_id"),
    ("capital_flow_evaluations", "evaluation_id"),
    ("factor_bundle_snapshots", "bundle_id"),
    ("shadow_composite_snapshots", "composite_id"),
    ("factor_correlation_audits", "correlation_audit_id"),
    ("orchestration_evaluations", "evaluation_id"),
)


class FactorOutputRepository:
    def __init__(self) -> None:
        initialize_database()

    @staticmethod
    def _row_to_factor(row: tuple[Any, ...]) -> FactorOutput:
        return FactorOutput(
            factor_id=row[0],
            symbol=row[1],
            factor_type=row[2],
            score=row[3],
            confidence=row[4],
            data_cutoff=row[5],
            generated_at=row[6],
            evidence_ids=_json_load(row[7]),
            risk_flags=_json_load(row[8]),
            model_call_ids=_json_load(row[9]),
            algorithm_version=row[10],
            input_snapshot_hash=row[11],
            shadow_mode=row[12],
            metadata=_json_load(row[13]),
        )

    def get(self, factor_id: str) -> FactorOutput | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT factor_id, symbol, factor_type, score, confidence,
                       data_cutoff, generated_at, evidence_ids_json,
                       risk_flags_json, model_call_ids_json,
                       algorithm_version, input_snapshot_hash,
                       shadow_mode, metadata_json
                FROM factor_outputs
                WHERE factor_id = ?
                """,
                [factor_id],
            ).fetchone()
        return None if row is None else self._row_to_factor(row)

    @staticmethod
    def resolve_evidence(evidence_id: str) -> dict[str, Any] | None:
        with get_connection() as connection:
            for table, column in _FACTOR_EVIDENCE_QUERIES:
                row = connection.execute(
                    f"SELECT * FROM {table} WHERE {column} = ?",
                    [evidence_id],
                ).fetchone()
                if row is not None:
                    return {"layer": table, "evidence_id": evidence_id}
        return None

    def _validate_references(self, factor: FactorOutput) -> None:
        unique_evidence = list(dict.fromkeys(factor.evidence_ids))
        found: set[str] = set()
        with get_connection() as connection:
            placeholders = _placeholders(unique_evidence)
            for table, column in _FACTOR_EVIDENCE_QUERIES:
                rows = connection.execute(
                    f"""
                    SELECT {column}
                    FROM {table}
                    WHERE {column} IN ({placeholders})
                    """,
                    unique_evidence,
                ).fetchall()
                found.update(str(row[0]) for row in rows)
        missing = [
            evidence_id
            for evidence_id in unique_evidence
            if evidence_id not in found
        ]
        if missing:
            raise EvidenceNotFoundError(
                "unknown factor evidence IDs: " + ", ".join(missing)
            )
        if factor.model_call_ids:
            with get_connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT call_id FROM model_calls
                    WHERE call_id IN ({_placeholders(factor.model_call_ids)})
                    """,
                    factor.model_call_ids,
                ).fetchall()
            found = {row[0] for row in rows}
            missing_calls = [
                call_id
                for call_id in factor.model_call_ids
                if call_id not in found
            ]
            if missing_calls:
                raise EvidenceNotFoundError(
                    "unknown factor model call IDs: "
                    + ", ".join(missing_calls)
                )

    def save(self, factor: FactorOutput) -> FactorOutput:
        self._validate_references(factor)
        with get_connection() as connection:
            existing = connection.execute(
                """
                SELECT factor_id
                FROM factor_outputs
                WHERE factor_id = ?
                   OR (
                       factor_type = ? AND symbol = ?
                       AND data_cutoff = ? AND algorithm_version = ?
                       AND input_snapshot_hash = ? AND shadow_mode = ?
                   )
                ORDER BY CASE WHEN factor_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                [
                    factor.factor_id,
                    factor.factor_type.value,
                    factor.symbol,
                    factor.data_cutoff,
                    factor.algorithm_version,
                    factor.input_snapshot_hash,
                    factor.shadow_mode,
                    factor.factor_id,
                ],
            ).fetchone()
            if existing is not None:
                persisted = self.get(existing[0])
                if persisted is None:
                    raise UnifiedRepositoryError(
                        "existing factor output could not be read"
                    )
                return persisted
            connection.execute(
                """
                INSERT INTO factor_outputs (
                    factor_id, symbol, factor_type, score, confidence,
                    data_cutoff, generated_at, evidence_ids_json,
                    risk_flags_json, model_call_ids_json,
                    algorithm_version, input_snapshot_hash,
                    shadow_mode, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    factor.factor_id,
                    factor.symbol,
                    factor.factor_type.value,
                    factor.score,
                    factor.confidence,
                    factor.data_cutoff,
                    factor.generated_at,
                    _json_dump(factor.evidence_ids),
                    _json_dump(factor.risk_flags),
                    _json_dump(factor.model_call_ids),
                    factor.algorithm_version,
                    factor.input_snapshot_hash,
                    factor.shadow_mode,
                    _json_dump(factor.metadata),
                ],
            )
        persisted = self.get(factor.factor_id)
        if persisted is None:
            raise UnifiedRepositoryError("factor output was not persisted")
        return persisted

    def list(
        self,
        *,
        symbol: str | None = None,
        factor_type: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        data_cutoff: datetime | None = None,
    ) -> list[FactorOutput]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if symbol is not None:
            clauses.append("symbol = ?")
            parameters.append(symbol)
        if factor_type is not None:
            clauses.append("factor_type = ?")
            parameters.append(factor_type)
        if start_time is not None:
            clauses.append("data_cutoff >= ?")
            parameters.append(start_time)
        if end_time is not None:
            clauses.append("data_cutoff <= ?")
            parameters.append(end_time)
        if data_cutoff is not None:
            clauses.append("data_cutoff <= ?")
            parameters.append(data_cutoff)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT factor_id, symbol, factor_type, score, confidence,
                       data_cutoff, generated_at, evidence_ids_json,
                       risk_flags_json, model_call_ids_json,
                       algorithm_version, input_snapshot_hash,
                       shadow_mode, metadata_json
                FROM factor_outputs
                {where}
                ORDER BY data_cutoff, factor_id
                """,
                parameters,
            ).fetchall()
        return [self._row_to_factor(row) for row in rows]


__all__ = [
    "CanonicalFinancialRepository",
    "CanonicalMarketRepository",
    "EventClusterRepository",
    "EvidenceNotFoundError",
    "FactorOutputRepository",
    "UnifiedRepositoryError",
]
