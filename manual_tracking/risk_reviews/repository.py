from __future__ import annotations

import json
from datetime import date
from typing import Any

from database.db import get_connection, initialize_database
from trading.schemas import ManualPositionRiskReview


class ManualPositionRiskRepositoryError(RuntimeError):
    pass


class ManualPositionRiskReviewNotFoundError(ManualPositionRiskRepositoryError):
    pass


class ManualPositionRiskEvidenceError(ManualPositionRiskRepositoryError):
    pass


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class ManualPositionRiskReviewRepository:
    """Append and read manual-position risk reviews without touching ledgers."""

    def __init__(self) -> None:
        initialize_database()

    @staticmethod
    def _validate_references(
        *,
        evidence_record_ids: list[str],
        model_call_ids: list[str],
    ) -> None:
        with get_connection() as connection:
            if evidence_record_ids:
                placeholders = ", ".join("?" for _ in evidence_record_ids)
                rows = connection.execute(
                    f"""
                    SELECT record_id
                    FROM data_records
                    WHERE record_id IN ({placeholders})
                    """,
                    evidence_record_ids,
                ).fetchall()
                found = {row[0] for row in rows}
                missing = [
                    record_id
                    for record_id in evidence_record_ids
                    if record_id not in found
                ]
                if missing:
                    raise ManualPositionRiskEvidenceError(
                        "unknown evidence_record_ids: " + ", ".join(missing)
                    )
            if model_call_ids:
                placeholders = ", ".join("?" for _ in model_call_ids)
                rows = connection.execute(
                    f"""
                    SELECT call_id
                    FROM model_calls
                    WHERE call_id IN ({placeholders})
                    """,
                    model_call_ids,
                ).fetchall()
                found = {row[0] for row in rows}
                missing = [
                    call_id for call_id in model_call_ids if call_id not in found
                ]
                if missing:
                    raise ManualPositionRiskEvidenceError(
                        "unknown model_call_ids: " + ", ".join(missing)
                    )

    def append(
        self,
        review: ManualPositionRiskReview,
        *,
        created_by: str,
        channel: str,
    ) -> ManualPositionRiskReview:
        if not created_by.strip() or not channel.strip():
            raise ManualPositionRiskRepositoryError(
                "authenticated reviewer and channel are required"
            )
        self._validate_references(
            evidence_record_ids=review.evidence_record_ids,
            model_call_ids=review.model_call_ids,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO manual_position_risk_reviews (
                    review_id, position_id, reviewed_at, data_cutoff_time,
                    risk_level, original_thesis_status, recommended_action,
                    evidence_record_ids_json, model_call_ids_json,
                    review_json, created_by, channel
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    review.review_id,
                    review.position_id,
                    review.reviewed_at,
                    review.data_cutoff_time,
                    review.risk_level.value,
                    review.original_thesis_status.value,
                    review.recommended_action.value,
                    _json_dump(review.evidence_record_ids),
                    _json_dump(review.model_call_ids),
                    review.model_dump_json(),
                    created_by,
                    channel,
                ],
            )
        return review

    @staticmethod
    def _row_to_review(row: tuple[Any, ...]) -> ManualPositionRiskReview:
        payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        return ManualPositionRiskReview.model_validate(payload)

    def list_for_position(
        self,
        position_id: str,
    ) -> list[ManualPositionRiskReview]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT review_json
                FROM manual_position_risk_reviews
                WHERE position_id = ?
                ORDER BY reviewed_at, review_id
                """,
                [position_id],
            ).fetchall()
        return [self._row_to_review(row) for row in rows]

    def latest_for_position(
        self,
        position_id: str,
    ) -> ManualPositionRiskReview:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT review_json
                FROM manual_position_risk_reviews
                WHERE position_id = ?
                ORDER BY reviewed_at DESC, review_id DESC
                LIMIT 1
                """,
                [position_id],
            ).fetchone()
        if row is None:
            raise ManualPositionRiskReviewNotFoundError(
                f"manual position risk review not found: {position_id}"
            )
        return self._row_to_review(row)

    def list_for_date(
        self,
        review_date: date,
    ) -> list[ManualPositionRiskReview]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT review_json
                FROM manual_position_risk_reviews
                WHERE CAST(reviewed_at AS DATE) = ?
                ORDER BY reviewed_at, review_id
                """,
                [review_date],
            ).fetchall()
        return [self._row_to_review(row) for row in rows]
