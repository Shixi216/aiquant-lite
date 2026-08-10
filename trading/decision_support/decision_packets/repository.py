from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any
from uuid import uuid4

import duckdb

from database.db import get_connection, initialize_database
from trading.schemas import (
    AdversarialReview,
    DecisionPacket,
    DecisionStatus,
)


logger = logging.getLogger(__name__)

PAYLOAD_FIELDS = {
    "decision",
    "confidence",
    "evidence_quality",
    "verified_facts",
    "calculated_metrics",
    "model_inferences",
    "assumptions",
    "bull_case",
    "base_case",
    "bear_case",
    "entry_conditions",
    "invalidation_conditions",
    "risk_flags",
    "suggested_position_limit",
    "holding_horizon",
    "missing_information",
    "agent_disagreements",
    "factor_output_ids",
    "analysis_mode",
    "fundamental_data_status",
    "used_report_period",
    "announcement_time",
    "data_available_time",
    "missing_fields",
    "factor_output_id",
    "verification_status",
    "auto_fetch_attempted",
    "auto_fetch_result",
    "manual_fundamental_audit_id",
}


class DecisionRepositoryError(RuntimeError):
    pass


class DecisionNotFoundError(DecisionRepositoryError):
    pass


class DecisionIntegrityError(DecisionRepositoryError):
    pass


class DecisionImmutableError(DecisionRepositoryError):
    pass


class DecisionVersionError(DecisionRepositoryError):
    pass


class SourceTraceabilityError(DecisionRepositoryError):
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


class DecisionRepository:
    """Append-only DecisionPacket persistence with verified reads."""

    def __init__(self) -> None:
        initialize_database()

    def get_source_records(self, record_ids: list[str]) -> list[dict[str, Any]]:
        unique_ids = list(dict.fromkeys(record_ids))
        if not unique_ids:
            raise SourceTraceabilityError("source_record_ids must not be empty")
        placeholders = ", ".join("?" for _ in unique_ids)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT record_id, symbol, data_type, event_time, fetched_at,
                       source_name, source_url, source_level, verified,
                       content_hash, payload_json
                FROM data_records
                WHERE record_id IN ({placeholders})
                """,
                unique_ids,
            ).fetchall()
        records = {
            row[0]: {
                "record_id": row[0],
                "symbol": row[1],
                "data_type": row[2],
                "event_time": row[3],
                "fetched_at": row[4],
                "source_name": row[5],
                "source_url": row[6],
                "source_level": row[7],
                "verified": row[8],
                "content_hash": row[9],
                "payload": _json_load(row[10]),
            }
            for row in rows
        }
        missing = [record_id for record_id in unique_ids if record_id not in records]
        if missing:
            raise SourceTraceabilityError(
                "unknown source_record_ids: " + ", ".join(missing)
            )
        return [records[record_id] for record_id in unique_ids]

    def _validate_model_calls(
        self,
        connection: duckdb.DuckDBPyConnection,
        model_call_ids: list[str],
    ) -> None:
        if not model_call_ids:
            return
        placeholders = ", ".join("?" for _ in model_call_ids)
        rows = connection.execute(
            f"SELECT call_id FROM model_calls WHERE call_id IN ({placeholders})",
            model_call_ids,
        ).fetchall()
        found = {row[0] for row in rows}
        missing = [call_id for call_id in model_call_ids if call_id not in found]
        if missing:
            raise SourceTraceabilityError(
                "unknown model_call_ids: " + ", ".join(missing)
            )

    def _validate_factor_outputs(
        self,
        connection: duckdb.DuckDBPyConnection,
        factor_output_ids: list[str],
        data_cutoff_time: datetime,
    ) -> None:
        if not factor_output_ids:
            return
        placeholders = ", ".join("?" for _ in factor_output_ids)
        rows = connection.execute(
            f"""
            SELECT factor_id, data_cutoff
            FROM factor_outputs
            WHERE factor_id IN ({placeholders})
            """,
            factor_output_ids,
        ).fetchall()
        found = {row[0]: row[1] for row in rows}
        missing = [
            factor_id
            for factor_id in factor_output_ids
            if factor_id not in found
        ]
        if missing:
            raise SourceTraceabilityError(
                "unknown factor_output_ids: " + ", ".join(missing)
            )
        later = [
            factor_id
            for factor_id in factor_output_ids
            if found[factor_id] > data_cutoff_time
        ]
        if later:
            raise SourceTraceabilityError(
                "factor outputs exceed DecisionPacket data_cutoff_time: "
                + ", ".join(later)
            )

    @staticmethod
    def _packet_payload(packet: DecisionPacket) -> dict[str, Any]:
        dumped = packet.model_dump(mode="json")
        return {field: dumped[field] for field in PAYLOAD_FIELDS}

    @staticmethod
    def _verified_source_ids(packet: DecisionPacket) -> set[str]:
        return {
            source_record_id
            for fact in packet.verified_facts
            for source_record_id in fact.source_record_ids
        }

    def _insert_related_records(
        self,
        connection: duckdb.DuckDBPyConnection,
        packet: DecisionPacket,
        source_records: list[dict[str, Any]],
    ) -> None:
        for record in source_records:
            connection.execute(
                """
                INSERT INTO decision_evidence (
                    decision_id, decision_version, source_record_id,
                    verified_snapshot, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    packet.decision_id,
                    packet.decision_version,
                    record["record_id"],
                    record["verified"],
                    packet.generated_at,
                ],
            )
        for index, opinion in enumerate(packet.decision.opinions):
            connection.execute(
                """
                INSERT INTO agent_opinions (
                    decision_id, decision_version, opinion_index,
                    agent_role, opinion_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    packet.decision_id,
                    packet.decision_version,
                    index,
                    opinion.role,
                    _json_dump(opinion.model_dump(mode="json")),
                    packet.generated_at,
                ],
            )
        for factor_id in packet.factor_output_ids:
            connection.execute(
                """
                INSERT INTO decision_factor_outputs (
                    decision_id, decision_version, factor_id, created_at
                )
                VALUES (?, ?, ?, ?)
                """,
                [
                    packet.decision_id,
                    packet.decision_version,
                    factor_id,
                    packet.generated_at,
                ],
            )
        connection.execute(
            """
            INSERT INTO risk_vetoes (
                decision_id, decision_version, vetoed, risk_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                packet.decision_id,
                packet.decision_version,
                packet.decision.risk_verdict.vetoed,
                _json_dump(packet.decision.risk_verdict.model_dump(mode="json")),
                packet.generated_at,
            ],
        )
        connection.execute(
            """
            INSERT INTO adversarial_reviews (
                review_id, decision_id, decision_version, review_kind,
                challenge_text, review_json, data_cutoff_time,
                source_record_ids_json, model_call_ids_json,
                created_version, created_at
            )
            VALUES (?, ?, ?, 'INITIAL', NULL, ?, ?, ?, ?, NULL, ?)
            """,
            [
                f"{packet.decision_id}:{packet.decision_version}:initial",
                packet.decision_id,
                packet.decision_version,
                _json_dump(packet.decision.adversarial_review.model_dump(mode="json")),
                packet.data_cutoff_time,
                _json_dump(packet.source_record_ids),
                _json_dump(packet.model_call_ids),
                packet.generated_at,
            ],
        )
        trace = packet.decision
        connection.execute(
            """
            INSERT INTO decision_traces (
                trace_id, symbol, as_of_date, final_action,
                vetoed, trace_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (trace_id) DO NOTHING
            """,
            [
                trace.trace_id,
                trace.symbol,
                trace.as_of,
                trace.final_action.value,
                trace.risk_verdict.vetoed,
                trace.model_dump_json(),
                trace.created_at,
            ],
        )

    def insert(self, packet: DecisionPacket) -> DecisionPacket:
        if not packet.hash_is_valid():
            raise DecisionIntegrityError("packet_hash does not match DecisionPacket content")
        if packet.status == DecisionStatus.SUPERSEDED:
            raise DecisionVersionError("new packets cannot be inserted as SUPERSEDED")

        source_records = self.get_source_records(packet.source_record_ids)
        actual_verified_ids = {
            record["record_id"] for record in source_records if record["verified"]
        }
        if self._verified_source_ids(packet) != actual_verified_ids:
            raise SourceTraceabilityError(
                "verified_facts must exactly match verified source records"
            )
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                self._validate_model_calls(connection, packet.model_call_ids)
                self._validate_factor_outputs(
                    connection,
                    packet.factor_output_ids,
                    packet.data_cutoff_time,
                )
                rows = connection.execute(
                    """
                    SELECT decision_version
                    FROM decision_packets
                    WHERE decision_id = ?
                    ORDER BY decision_version
                    """,
                    [packet.decision_id],
                ).fetchall()
                versions = [int(row[0]) for row in rows]
                if packet.decision_version in versions:
                    raise DecisionImmutableError(
                        "DecisionPacket versions are immutable and cannot be overwritten"
                    )
                if not versions:
                    if packet.decision_version != 1:
                        raise DecisionVersionError("first DecisionPacket version must be 1")
                else:
                    latest_version = versions[-1]
                    if packet.decision_version != latest_version + 1:
                        raise DecisionVersionError(
                            "new DecisionPacket version must increment the latest version"
                        )
                    if packet.supersedes_version != latest_version:
                        raise DecisionVersionError(
                            "supersedes_version must reference the latest version"
                        )

                payload = self._packet_payload(packet)
                connection.execute(
                    """
                    INSERT INTO decision_packets (
                        decision_id, decision_version, supersedes_version,
                        symbol, generated_at, data_cutoff_time, packet_hash,
                        schema_version, generator_version, status, payload_json,
                        source_record_ids_json, model_call_ids_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        packet.decision_id,
                        packet.decision_version,
                        packet.supersedes_version,
                        packet.symbol,
                        packet.generated_at,
                        packet.data_cutoff_time,
                        packet.packet_hash,
                        packet.schema_version,
                        packet.generator_version,
                        packet.status.value,
                        _json_dump(payload),
                        _json_dump(packet.source_record_ids),
                        _json_dump(packet.model_call_ids),
                        packet.generated_at,
                    ],
                )
                self._insert_related_records(connection, packet, source_records)
                if packet.supersedes_version is not None:
                    previous = self._load_packet(
                        connection,
                        packet.decision_id,
                        packet.supersedes_version,
                        validate_related=True,
                    )
                    if previous.status != DecisionStatus.FINAL:
                        raise DecisionVersionError(
                            "only a FINAL DecisionPacket can be superseded"
                        )
                    superseded = previous.rehashed_copy(
                        status=DecisionStatus.SUPERSEDED
                    )
                    connection.execute(
                        """
                        UPDATE decision_packets
                        SET status = ?, packet_hash = ?
                        WHERE decision_id = ? AND decision_version = ?
                        """,
                        [
                            superseded.status.value,
                            superseded.packet_hash,
                            superseded.decision_id,
                            superseded.decision_version,
                        ],
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return packet

    def _row_to_packet(self, row: tuple[Any, ...]) -> DecisionPacket:
        payload = _json_load(row[10])
        values = {
            "decision_id": row[0],
            "decision_version": int(row[1]),
            "supersedes_version": int(row[2]) if row[2] is not None else None,
            "symbol": row[3],
            "generated_at": row[4],
            "data_cutoff_time": row[5],
            "packet_hash": row[6],
            "schema_version": row[7],
            "generator_version": row[8],
            "status": row[9],
            **payload,
            "source_record_ids": _json_load(row[11]),
            "model_call_ids": _json_load(row[12]),
        }
        return DecisionPacket.model_validate(values)

    def _load_packet(
        self,
        connection: duckdb.DuckDBPyConnection,
        decision_id: str,
        version: int,
        *,
        validate_related: bool,
    ) -> DecisionPacket:
        row = connection.execute(
            """
            SELECT decision_id, decision_version, supersedes_version,
                   symbol, generated_at, data_cutoff_time, packet_hash,
                   schema_version, generator_version, status, payload_json,
                   source_record_ids_json, model_call_ids_json
            FROM decision_packets
            WHERE decision_id = ? AND decision_version = ?
            """,
            [decision_id, version],
        ).fetchone()
        if row is None:
            raise DecisionNotFoundError(
                f"DecisionPacket not found: {decision_id} version {version}"
            )
        try:
            packet = self._row_to_packet(row)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise DecisionIntegrityError(
                f"persisted DecisionPacket cannot be decoded: {exc}"
            ) from exc
        if not packet.hash_is_valid():
            raise DecisionIntegrityError("packet_hash mismatch")
        if validate_related:
            self._validate_related_records(connection, packet)
        return packet

    def _validate_related_records(
        self,
        connection: duckdb.DuckDBPyConnection,
        packet: DecisionPacket,
    ) -> None:
        evidence_rows = connection.execute(
            """
            SELECT source_record_id, verified_snapshot
            FROM decision_evidence
            WHERE decision_id = ? AND decision_version = ?
            ORDER BY source_record_id
            """,
            [packet.decision_id, packet.decision_version],
        ).fetchall()
        evidence_snapshots = sorted(
            (row[0], bool(row[1]))
            for row in evidence_rows
        )
        verified_source_ids = self._verified_source_ids(packet)
        expected_snapshots = sorted(
            (source_record_id, source_record_id in verified_source_ids)
            for source_record_id in packet.source_record_ids
        )
        if evidence_snapshots != expected_snapshots:
            raise DecisionIntegrityError(
                "decision_evidence does not match packet source snapshots"
            )

        opinion_rows = connection.execute(
            """
            SELECT opinion_json
            FROM agent_opinions
            WHERE decision_id = ? AND decision_version = ?
            ORDER BY opinion_index
            """,
            [packet.decision_id, packet.decision_version],
        ).fetchall()
        stored_opinions = [_json_load(row[0]) for row in opinion_rows]
        expected_opinions = [
            opinion.model_dump(mode="json")
            for opinion in packet.decision.opinions
        ]
        if stored_opinions != expected_opinions:
            raise DecisionIntegrityError("agent_opinions do not match packet payload")

        risk_row = connection.execute(
            """
            SELECT risk_json
            FROM risk_vetoes
            WHERE decision_id = ? AND decision_version = ?
            """,
            [packet.decision_id, packet.decision_version],
        ).fetchone()
        expected_risk = packet.decision.risk_verdict.model_dump(mode="json")
        if risk_row is None or _json_load(risk_row[0]) != expected_risk:
            raise DecisionIntegrityError("risk_vetoes do not match packet payload")

        review_row = connection.execute(
            """
            SELECT review_json
            FROM adversarial_reviews
            WHERE decision_id = ? AND decision_version = ?
              AND review_kind = 'INITIAL'
            """,
            [packet.decision_id, packet.decision_version],
        ).fetchone()
        expected_review = packet.decision.adversarial_review.model_dump(mode="json")
        if review_row is None or _json_load(review_row[0]) != expected_review:
            raise DecisionIntegrityError(
                "initial adversarial review does not match packet payload"
            )

        factor_rows = connection.execute(
            """
            SELECT factor_id
            FROM decision_factor_outputs
            WHERE decision_id = ? AND decision_version = ?
            ORDER BY factor_id
            """,
            [packet.decision_id, packet.decision_version],
        ).fetchall()
        if [row[0] for row in factor_rows] != sorted(packet.factor_output_ids):
            raise DecisionIntegrityError(
                "decision_factor_outputs do not match packet payload"
            )

    def _record_integrity_failure(
        self,
        decision_id: str,
        version: int,
        detail: str,
    ) -> None:
        logger.error(
            "DecisionPacket integrity failure for %s version %s: %s",
            decision_id,
            version,
            detail,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO decision_integrity_audit (
                    audit_id, decision_id, decision_version,
                    event_type, details, created_at
                )
                VALUES (?, ?, ?, 'INTEGRITY_FAILURE', ?, ?)
                """,
                [
                    uuid4().hex,
                    decision_id,
                    version,
                    detail,
                    datetime.now().astimezone(),
                ],
            )

    def get(self, decision_id: str, version: int) -> DecisionPacket:
        try:
            with get_connection() as connection:
                return self._load_packet(
                    connection,
                    decision_id,
                    version,
                    validate_related=True,
                )
        except DecisionIntegrityError as exc:
            self._record_integrity_failure(decision_id, version, str(exc))
            raise

    def get_latest(self, decision_id: str) -> DecisionPacket:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT MAX(decision_version)
                FROM decision_packets
                WHERE decision_id = ?
                """,
                [decision_id],
            ).fetchone()
        if row is None or row[0] is None:
            raise DecisionNotFoundError(f"DecisionPacket not found: {decision_id}")
        return self.get(decision_id, int(row[0]))

    def list_versions(self, decision_id: str) -> list[DecisionPacket]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT decision_version
                FROM decision_packets
                WHERE decision_id = ?
                ORDER BY decision_version
                """,
                [decision_id],
            ).fetchall()
        if not rows:
            raise DecisionNotFoundError(f"DecisionPacket not found: {decision_id}")
        return [self.get(decision_id, int(row[0])) for row in rows]

    def record_challenge(
        self,
        *,
        challenge_id: str,
        decision_id: str,
        decision_version: int,
        challenge_text: str,
        review: AdversarialReview,
        data_cutoff_time: datetime,
        source_record_ids: list[str],
        model_call_ids: list[str],
        created_version: int | None,
        created_at: datetime,
    ) -> None:
        self.get_source_records(source_record_ids)
        with get_connection() as connection:
            self._validate_model_calls(connection, model_call_ids)
            connection.execute(
                """
                INSERT INTO adversarial_reviews (
                    review_id, decision_id, decision_version, review_kind,
                    challenge_text, review_json, data_cutoff_time,
                    source_record_ids_json, model_call_ids_json,
                    created_version, created_at
                )
                VALUES (?, ?, ?, 'CHALLENGE', ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    challenge_id,
                    decision_id,
                    decision_version,
                    challenge_text,
                    _json_dump(review.model_dump(mode="json")),
                    data_cutoff_time,
                    _json_dump(source_record_ids),
                    _json_dump(model_call_ids),
                    created_version,
                    created_at,
                ],
            )
