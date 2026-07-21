from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any

from database.db import get_connection, initialize_database
from data_hub.schemas.market import DataType, SourceLevel
from data_hub.schemas.service import (
    MarketFactEvidence,
    MarketFactVerificationResponse,
)


_FIELD_SEGMENT = re.compile(r"^[\w\-\u4e00-\u9fff]+$", re.UNICODE)


def _canonical_symbol(value: str) -> str:
    normalized = value.strip().upper()
    code, separator, exchange = normalized.partition(".")

    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"Invalid A-share symbol: {value}")

    if not separator:
        if code.startswith(("5", "6", "9")):
            exchange = "SH"
        elif code.startswith(("4", "8")):
            exchange = "BJ"
        else:
            exchange = "SZ"

    if exchange not in {"SH", "SZ", "BJ"}:
        raise ValueError(f"Unsupported exchange: {exchange}")

    return f"{code}.{exchange}"


def _normalize_event_date(value: str | None) -> str | None:
    if value is None:
        return None

    normalized = value.strip().replace("/", "-")
    try:
        return date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise ValueError("event_date must use YYYY-MM-DD format") from exc


def _field_path(value: str) -> tuple[str, ...]:
    parts = tuple(part.strip() for part in value.split(".") if part.strip())

    if not parts or len(parts) > 8 or any(not _FIELD_SEGMENT.match(part) for part in parts):
        raise ValueError("field must be a dot-separated payload path")

    return parts


def _extract(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload

    for part in path:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]

    return current


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None

    try:
        result = float(value)
    except (TypeError, ValueError):
        return None

    return None if not math.isfinite(result) else result


def _matches(observed: Any, expected: Any, tolerance: float) -> bool:
    observed_number = _number(observed)
    expected_number = _number(expected)

    if observed_number is not None and expected_number is not None:
        absolute_limit = max(abs(expected_number) * tolerance, tolerance)
        return abs(observed_number - expected_number) <= absolute_limit

    if isinstance(expected, bool):
        return observed is expected

    observed_text = "".join(str(observed or "").split()).casefold()
    expected_text = "".join(str(expected or "").split()).casefold()
    return bool(observed_text) and observed_text == expected_text


class MarketFactService:
    """Verify a market fact against persisted, source-attributed records."""

    def verify_market_fact(
        self,
        symbol: str,
        data_type: str,
        field: str,
        expected_value: Any,
        event_date: str | None = None,
        tolerance: float = 0.005,
        limit: int = 100,
    ) -> MarketFactVerificationResponse:
        canonical_symbol = _canonical_symbol(symbol)
        normalized_date = _normalize_event_date(event_date)
        path = _field_path(field)

        try:
            normalized_type = DataType(data_type).value
        except ValueError as exc:
            allowed = ", ".join(item.value for item in DataType)
            raise ValueError(f"Unsupported data_type; expected one of: {allowed}") from exc

        if not 0 <= tolerance <= 0.25:
            raise ValueError("tolerance must be between 0 and 0.25")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")

        initialize_database()
        clauses = ["symbol = ?", "data_type = ?"]
        parameters: list[Any] = [canonical_symbol, normalized_type]

        if normalized_date:
            clauses.append("CAST(event_time AS DATE) = CAST(? AS DATE)")
            parameters.append(normalized_date)

        parameters.append(limit)
        query = f"""
            SELECT
                record_id,
                source_name,
                source_url,
                source_level,
                event_time,
                verified,
                payload_json
            FROM data_records
            WHERE {' AND '.join(clauses)}
            ORDER BY event_time DESC, fetched_at DESC
            LIMIT ?
        """

        with get_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        evidence: list[MarketFactEvidence] = []

        for row in rows:
            payload = row[6]
            if isinstance(payload, str):
                payload = json.loads(payload)
            if not isinstance(payload, dict):
                continue

            observed = _extract(payload, path)
            if observed is None:
                continue

            evidence.append(
                MarketFactEvidence(
                    record_id=row[0],
                    source_name=row[1],
                    source_url=row[2],
                    source_level=row[3],
                    event_time=row[4],
                    record_verified=bool(row[5]),
                    observed_value=observed,
                    matched=_matches(observed, expected_value, tolerance),
                )
            )

        matched_sources = sorted({item.source_name for item in evidence if item.matched})
        conflicting_sources = sorted(
            {item.source_name for item in evidence if not item.matched}
        )
        official_match = any(
            item.matched
            and item.record_verified
            and item.source_level == SourceLevel.OFFICIAL.value
            for item in evidence
        )
        corroborated = len(matched_sources) >= 2

        if official_match or corroborated:
            verdict = "verified"
            confidence = 1.0 if official_match else min(0.95, 0.65 + 0.1 * len(matched_sources))
        elif conflicting_sources:
            verdict = "conflicting"
            confidence = 0.2
        else:
            verdict = "insufficient_evidence"
            confidence = 0.3 if matched_sources else 0.0

        return MarketFactVerificationResponse(
            symbol=canonical_symbol,
            data_type=normalized_type,
            field=field,
            expected_value=expected_value,
            event_date=normalized_date,
            verdict=verdict,
            confidence=confidence,
            matched_sources=matched_sources,
            conflicting_sources=conflicting_sources,
            evidence=evidence,
        )
