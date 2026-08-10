from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config.settings import Settings, settings
from data_hub.repositories import (
    CanonicalFinancialRepository,
    CanonicalMarketRepository,
    resolve_persisted_raw_records,
)
from data_hub.schemas.market import DataType, MarketRecord
from data_hub.schemas.unified import (
    CanonicalFinancialRecord,
    CanonicalMarketRecord,
    VerificationStatus,
)


CANONICAL_ALGORITHM_VERSION = "canonical-facts-v1"
MARKET_PRICE_FIELDS = ("open", "high", "low", "close")
MARKET_VOLUME_FIELDS = ("volume", "amount")


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _same_value(primary: Any, observed: Any, tolerance: float) -> tuple[bool, float | None]:
    primary_number = _as_number(primary)
    observed_number = _as_number(observed)
    if primary_number is not None and observed_number is not None:
        limit = max(abs(primary_number) * tolerance, tolerance)
        return abs(primary_number - observed_number) <= limit, limit
    if primary is None or observed is None:
        return primary is observed, None
    return str(primary).strip().casefold() == str(observed).strip().casefold(), None


def _priority(value: str) -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in value.split(",")
        if item.strip()
    )


def _priority_index(source_name: str, priorities: tuple[str, ...]) -> int:
    normalized = source_name.casefold()
    for index, candidate in enumerate(priorities):
        if normalized == candidate.casefold() or normalized.startswith(
            candidate.casefold() + " /"
        ):
            return index
    return len(priorities)


def _market_payload(record: MarketRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for field in ("trade_date", *MARKET_PRICE_FIELDS):
        value = record.data.get(field)
        if value is not None:
            payload[field] = value

    volume = record.data.get("volume")
    if volume is None and record.data.get("vol") is not None:
        volume = record.data.get("vol")
    amount = record.data.get("amount")
    if record.data_type == DataType.DAILY_BAR:
        raw_volume = _as_number(volume)
        raw_amount = _as_number(amount)
        source = record.source_name.casefold()
        if raw_volume is not None and (
            source.startswith("tushare pro")
            or source.startswith("akshare / eastmoney")
        ):
            volume = raw_volume * 100
        if raw_amount is not None and source.startswith("tushare pro"):
            amount = raw_amount * 1000
    if volume is not None:
        payload["volume"] = volume
    if amount is not None:
        payload["amount"] = amount
    return payload


@dataclass(frozen=True)
class CanonicalizationPolicy:
    market_source_priority: tuple[str, ...]
    financial_source_priority: tuple[str, ...]
    price_tolerance: float
    volume_tolerance: float
    financial_tolerance: float
    algorithm_version: str = CANONICAL_ALGORITHM_VERSION

    @classmethod
    def from_settings(
        cls,
        app_settings: Settings = settings,
    ) -> "CanonicalizationPolicy":
        return cls(
            market_source_priority=_priority(
                app_settings.canonical_market_source_priority
            ),
            financial_source_priority=_priority(
                app_settings.canonical_financial_source_priority
            ),
            price_tolerance=app_settings.canonical_price_tolerance,
            volume_tolerance=app_settings.canonical_volume_tolerance,
            financial_tolerance=app_settings.canonical_financial_tolerance,
        )


class CanonicalizationService:
    def __init__(
        self,
        *,
        policy: CanonicalizationPolicy | None = None,
        market_repository: CanonicalMarketRepository | None = None,
        financial_repository: CanonicalFinancialRepository | None = None,
        initialize_repositories: bool = True,
    ) -> None:
        self.policy = policy or CanonicalizationPolicy.from_settings()
        self.market_repository = market_repository
        self.financial_repository = financial_repository
        if initialize_repositories:
            self.market_repository = (
                self.market_repository or CanonicalMarketRepository()
            )
            self.financial_repository = (
                self.financial_repository
                or CanonicalFinancialRepository()
            )

    @staticmethod
    def _validate_group(records: list[MarketRecord]) -> list[MarketRecord]:
        if not records:
            raise ValueError("at least one raw record is required")
        symbols = {record.symbol for record in records}
        data_types = {record.data_type for record in records}
        event_times = {
            record.event_time.astimezone(timezone.utc)
            for record in records
        }
        if len(symbols) != 1:
            raise ValueError("canonical records must have one symbol")
        if len(data_types) != 1:
            raise ValueError("canonical records must have one data_type")
        if len(event_times) != 1:
            raise ValueError("canonical records must have one event_time")

        # A repeated fetch from the same provider is one source, never another
        # corroborating vote. Keep the newest raw record from each source.
        by_source: dict[str, MarketRecord] = {}
        for record in records:
            previous = by_source.get(record.source_name)
            if previous is None or record.fetched_at > previous.fetched_at:
                by_source[record.source_name] = record
        return list(by_source.values())

    @staticmethod
    def _canonical_id(
        prefix: str,
        symbol: str,
        data_type: str,
        event_time: datetime,
    ) -> str:
        identity = {
            "symbol": symbol,
            "data_type": data_type,
            "event_time": event_time.astimezone(timezone.utc).isoformat(),
        }
        return f"{prefix}_{_stable_hash(identity)[:32]}"

    @staticmethod
    def _generated_at(
        records: list[MarketRecord],
        requested: datetime | None,
    ) -> tuple[datetime, datetime]:
        data_cutoff = max(record.fetched_at for record in records)
        generated_at = requested or datetime.now().astimezone()
        if generated_at < data_cutoff:
            generated_at = data_cutoff
        return data_cutoff, generated_at

    def _compare(
        self,
        *,
        records: list[MarketRecord],
        priorities: tuple[str, ...],
        payloads: dict[str, dict[str, Any]],
        field_tolerances: dict[str, float],
        default_tolerance: float,
    ) -> tuple[
        MarketRecord,
        VerificationStatus,
        list[str],
        dict[str, Any],
        float,
    ]:
        ordered = sorted(
            records,
            key=lambda record: (
                _priority_index(record.source_name, priorities),
                record.source_name.casefold(),
                record.record_id,
            ),
        )
        primary = ordered[0]
        if len(ordered) == 1:
            return (
                primary,
                VerificationStatus.SINGLE_SOURCE,
                [],
                {},
                0.5,
            )

        primary_payload = payloads[primary.record_id]
        differences: dict[str, Any] = {}
        matching_sources: list[str] = []
        hard_conflict = False

        for candidate in ordered[1:]:
            candidate_payload = payloads[candidate.record_id]
            candidate_differences: dict[str, Any] = {}
            comparable = 0
            candidate_matches = True
            fields = sorted(set(primary_payload) | set(candidate_payload))
            for field in fields:
                primary_value = primary_payload.get(field)
                observed_value = candidate_payload.get(field)
                if primary_value is None or observed_value is None:
                    if primary_value is not observed_value:
                        candidate_differences[field] = {
                            "primary": primary_value,
                            "observed": observed_value,
                            "reason": "MISSING_VALUE",
                        }
                        candidate_matches = False
                    continue
                comparable += 1
                tolerance = field_tolerances.get(
                    field,
                    default_tolerance,
                )
                matches, limit = _same_value(
                    primary_value,
                    observed_value,
                    tolerance,
                )
                if not matches:
                    candidate_matches = False
                    hard_conflict = True
                    candidate_differences[field] = {
                        "primary": primary_value,
                        "observed": observed_value,
                        "tolerance": tolerance,
                        "absolute_limit": limit,
                        "reason": "OUTSIDE_TOLERANCE",
                    }
            if comparable == 0:
                candidate_matches = False
            if candidate_differences:
                differences[candidate.record_id] = {
                    "source": candidate.source_name,
                    "fields": candidate_differences,
                }
            if candidate_matches:
                matching_sources.append(candidate.record_id)

        if hard_conflict or not matching_sources:
            status = VerificationStatus.CONFLICT
            confidence = 0.0
            verification_ids = matching_sources
        else:
            status = VerificationStatus.VERIFIED
            confidence = min(1.0, 0.85 + 0.05 * len(matching_sources))
            verification_ids = [primary.record_id, *matching_sources]
        return (
            primary,
            status,
            verification_ids,
            differences,
            confidence,
        )

    def canonicalize_market(
        self,
        records: list[MarketRecord],
        *,
        generated_at: datetime | None = None,
        persist: bool = True,
    ) -> CanonicalMarketRecord:
        effective_records = (
            resolve_persisted_raw_records(records)
            if persist
            else records
        )
        normalized = self._validate_group(effective_records)
        if normalized[0].data_type == DataType.FINANCIAL_STATEMENT:
            raise ValueError("financial records require canonicalize_financial")
        if normalized[0].data_type in {
            DataType.ANNOUNCEMENT,
            DataType.FINANCE_NEWS,
        }:
            raise ValueError("news and announcements require event clustering")

        payloads = {
            record.record_id: _market_payload(record)
            for record in normalized
        }
        field_tolerances = {
            **{
                field: self.policy.price_tolerance
                for field in MARKET_PRICE_FIELDS
            },
            **{
                field: self.policy.volume_tolerance
                for field in MARKET_VOLUME_FIELDS
            },
        }
        primary, status, verification_ids, differences, confidence = (
            self._compare(
                records=normalized,
                priorities=self.policy.market_source_priority,
                payloads=payloads,
                field_tolerances=field_tolerances,
                default_tolerance=self.policy.price_tolerance,
            )
        )
        ordered_ids = [
            record.record_id
            for record in sorted(
                normalized,
                key=lambda item: (
                    item.record_id != primary.record_id,
                    item.source_name.casefold(),
                    item.record_id,
                ),
            )
        ]
        data_cutoff, actual_generated_at = self._generated_at(
            normalized,
            generated_at,
        )
        content = {
            "symbol": primary.symbol,
            "data_type": primary.data_type.value,
            "event_time": primary.event_time.astimezone(timezone.utc).isoformat(),
            "primary_source": primary.source_name,
            "source_record_ids": ordered_ids,
            "verification_source_ids": verification_ids,
            "verification_status": status.value,
            "field_differences": differences,
            "payload": payloads[primary.record_id],
            "algorithm_version": self.policy.algorithm_version,
        }
        record = CanonicalMarketRecord(
            canonical_record_id=self._canonical_id(
                "cmr",
                primary.symbol,
                primary.data_type.value,
                primary.event_time,
            ),
            symbol=primary.symbol,
            data_type=primary.data_type.value,
            event_time=primary.event_time,
            data_cutoff=data_cutoff,
            generated_at=actual_generated_at,
            primary_source=primary.source_name,
            source_record_ids=ordered_ids,
            verification_source_ids=verification_ids,
            verification_status=status,
            field_differences=differences,
            payload=payloads[primary.record_id],
            confidence=confidence,
            content_hash=_stable_hash(content),
            algorithm_version=self.policy.algorithm_version,
        )
        if not persist:
            return record
        if self.market_repository is None:
            raise RuntimeError("market repository is not initialized")
        return self.market_repository.save(record)

    def canonicalize_financial(
        self,
        records: list[MarketRecord],
        *,
        generated_at: datetime | None = None,
        persist: bool = True,
    ) -> CanonicalFinancialRecord:
        effective_records = (
            resolve_persisted_raw_records(records)
            if persist
            else records
        )
        normalized = self._validate_group(effective_records)
        if normalized[0].data_type != DataType.FINANCIAL_STATEMENT:
            raise ValueError("canonicalize_financial requires financial records")
        statement_types = {
            str(record.data.get("statement_type") or "").strip().casefold()
            for record in normalized
        }
        if len(statement_types) != 1:
            raise ValueError(
                "financial canonicalization requires one statement_type"
            )
        statement_type = next(iter(statement_types))
        canonical_data_type = (
            f"{DataType.FINANCIAL_STATEMENT.value}:{statement_type}"
            if statement_type
            else DataType.FINANCIAL_STATEMENT.value
        )
        payloads = {
            record.record_id: dict(record.data)
            for record in normalized
        }
        primary, status, verification_ids, differences, confidence = (
            self._compare(
                records=normalized,
                priorities=self.policy.financial_source_priority,
                payloads=payloads,
                field_tolerances={},
                default_tolerance=self.policy.financial_tolerance,
            )
        )
        ordered_ids = [
            record.record_id
            for record in sorted(
                normalized,
                key=lambda item: (
                    item.record_id != primary.record_id,
                    item.source_name.casefold(),
                    item.record_id,
                ),
            )
        ]
        data_cutoff, actual_generated_at = self._generated_at(
            normalized,
            generated_at,
        )
        content = {
            "symbol": primary.symbol,
            "data_type": canonical_data_type,
            "event_time": primary.event_time.astimezone(timezone.utc).isoformat(),
            "primary_source": primary.source_name,
            "source_record_ids": ordered_ids,
            "verification_source_ids": verification_ids,
            "verification_status": status.value,
            "field_differences": differences,
            "payload": payloads[primary.record_id],
            "algorithm_version": self.policy.algorithm_version,
        }
        record = CanonicalFinancialRecord(
            canonical_record_id=self._canonical_id(
                "cfr",
                primary.symbol,
                canonical_data_type,
                primary.event_time,
            ),
            symbol=primary.symbol,
            data_type=canonical_data_type,
            event_time=primary.event_time,
            data_cutoff=data_cutoff,
            generated_at=actual_generated_at,
            primary_source=primary.source_name,
            source_record_ids=ordered_ids,
            verification_source_ids=verification_ids,
            verification_status=status,
            field_differences=differences,
            payload=payloads[primary.record_id],
            confidence=confidence,
            content_hash=_stable_hash(content),
            algorithm_version=self.policy.algorithm_version,
        )
        if not persist:
            return record
        if self.financial_repository is None:
            raise RuntimeError("financial repository is not initialized")
        return self.financial_repository.save(record)


__all__ = [
    "CANONICAL_ALGORITHM_VERSION",
    "CanonicalizationPolicy",
    "CanonicalizationService",
]
