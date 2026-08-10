from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from data_hub.repositories import (
    EventClusterRepository,
    load_raw_records,
    resolve_persisted_raw_records,
)
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.unified import EventCluster


EVENT_DEDUP_VERSION = "event-dedup-v1"
EVENT_WINDOW = timedelta(hours=24)
_NON_WORD = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_SOURCE_RANK = {
    SourceLevel.OFFICIAL: 0,
    SourceLevel.STRUCTURED: 1,
    SourceLevel.PUBLIC_WEB: 2,
    SourceLevel.MEDIA: 3,
}


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_title(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return _NON_WORD.sub("", normalized)


def _title(record: MarketRecord) -> str:
    for field in ("title", "announcement_title", "news_title"):
        value = record.data.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise ValueError(f"event source {record.record_id} has no title")


def _symbols(record: MarketRecord) -> set[str]:
    values = (
        set()
        if record.symbol in {"MARKET.GLOBAL", "UNLINKED", "UNKNOWN"}
        else {record.symbol}
    )
    payload_symbols = record.data.get("symbols")
    if isinstance(payload_symbols, list):
        values.update(str(item).strip() for item in payload_symbols if str(item).strip())
    payload_symbol = record.data.get("symbol")
    if payload_symbol:
        values.add(str(payload_symbol).strip())
    return values


def _sectors(record: MarketRecord) -> set[str]:
    values: set[str] = set()
    sector = record.data.get("sector")
    if sector:
        values.add(str(sector).strip())
    sectors = record.data.get("sectors")
    if isinstance(sectors, list):
        values.update(str(item).strip() for item in sectors if str(item).strip())
    return values


class EventClusterService:
    def __init__(
        self,
        repository: EventClusterRepository | None = None,
    ) -> None:
        self.repository = repository or EventClusterRepository()

    @staticmethod
    def _validate_record(record: MarketRecord) -> None:
        if record.data_type not in {
            DataType.ANNOUNCEMENT,
            DataType.FINANCE_NEWS,
        }:
            raise ValueError("event clustering accepts only news or announcements")
        _title(record)

    @staticmethod
    def _is_same_event(
        cluster: EventCluster,
        record: MarketRecord,
    ) -> bool:
        if record.content_hash:
            existing_records = load_raw_records(cluster.source_record_ids)
            if any(
                item.content_hash == record.content_hash
                for item in existing_records
            ):
                return True
        if normalize_title(cluster.canonical_title) != normalize_title(_title(record)):
            return False
        cluster_symbols = set(cluster.symbol_links)
        record_symbols = _symbols(record)
        if not cluster_symbols and not record_symbols:
            return True
        return bool(cluster_symbols & record_symbols)

    def _find_cluster(self, record: MarketRecord) -> EventCluster | None:
        existing = self.repository.get_by_source_id(record.record_id)
        if existing is not None:
            return existing
        for cluster in self.repository.candidates(
            event_type=record.data_type.value,
            event_time=record.event_time,
            window=EVENT_WINDOW,
        ):
            if self._is_same_event(cluster, record):
                return cluster
        return None

    @staticmethod
    def _primary(records: list[MarketRecord]) -> MarketRecord:
        return min(
            records,
            key=lambda record: (
                _SOURCE_RANK[record.source_level],
                record.event_time,
                record.fetched_at,
                record.record_id,
            ),
        )

    @staticmethod
    def _cluster_id(record: MarketRecord) -> str:
        identity = {
            "event_type": record.data_type.value,
            "normalized_title": normalize_title(_title(record)),
            "symbols": sorted(_symbols(record)),
            "event_date": record.event_time.astimezone(timezone.utc)
            .date()
            .isoformat(),
        }
        return f"evt_{_stable_hash(identity)[:32]}"

    def _build(
        self,
        records: list[MarketRecord],
        *,
        cluster_id: str,
        generated_at: datetime | None,
    ) -> tuple[EventCluster, dict[str, str]]:
        unique = {record.record_id: record for record in records}
        ordered = sorted(unique.values(), key=lambda record: record.record_id)
        primary = self._primary(ordered)
        source_ids = [record.record_id for record in ordered]
        symbols = sorted(
            {
                symbol
                for record in ordered
                for symbol in _symbols(record)
            }
        )
        sectors = sorted(
            {
                sector
                for record in ordered
                for sector in _sectors(record)
            }
        )
        hashes = {
            record.content_hash
            for record in ordered
            if record.content_hash
        }
        method = (
            "CONTENT_HASH"
            if len(hashes) == 1 and len(ordered) > 1
            else "NORMALIZED_TITLE_TIME_ENTITY"
        )
        data_cutoff = max(record.fetched_at for record in ordered)
        actual_generated = generated_at or datetime.now().astimezone()
        if actual_generated < data_cutoff:
            actual_generated = data_cutoff
        cluster_content = {
            "event_cluster_id": cluster_id,
            "canonical_title": _title(primary),
            "event_type": primary.data_type.value,
            "event_time": primary.event_time.astimezone(timezone.utc).isoformat(),
            "primary_source_id": primary.record_id,
            "source_record_ids": source_ids,
            "symbol_links": symbols,
            "sector_links": sectors,
            "dedup_method": method,
            "dedup_version": EVENT_DEDUP_VERSION,
        }
        cluster = EventCluster(
            event_cluster_id=cluster_id,
            canonical_title=_title(primary),
            event_type=primary.data_type.value,
            event_time=primary.event_time,
            data_cutoff=data_cutoff,
            primary_source_id=primary.record_id,
            source_count=len(source_ids),
            source_record_ids=source_ids,
            symbol_links=symbols,
            sector_links=sectors,
            dedup_method=method,
            dedup_version=EVENT_DEDUP_VERSION,
            cluster_hash=_stable_hash(cluster_content),
            generated_at=actual_generated,
        )
        levels = {
            record.record_id: record.source_level.value
            for record in ordered
        }
        return cluster, levels

    def cluster(
        self,
        records: list[MarketRecord],
        *,
        generated_at: datetime | None = None,
    ) -> list[EventCluster]:
        results: dict[str, EventCluster] = {}
        effective_records = resolve_persisted_raw_records(records)
        for record in sorted(
            effective_records,
            key=lambda item: (item.event_time, item.record_id),
        ):
            self._validate_record(record)
            existing = self._find_cluster(record)
            if existing is None:
                grouped_records = [record]
                cluster_id = self._cluster_id(record)
            else:
                grouped_records = load_raw_records(existing.source_record_ids)
                if record.record_id not in {
                    item.record_id for item in grouped_records
                }:
                    grouped_records.append(record)
                cluster_id = existing.event_cluster_id
            cluster, levels = self._build(
                grouped_records,
                cluster_id=cluster_id,
                generated_at=generated_at,
            )
            results[cluster_id] = self.repository.save(cluster, levels)
        return sorted(
            results.values(),
            key=lambda cluster: (cluster.event_time, cluster.event_cluster_id),
        )


__all__ = [
    "EVENT_DEDUP_VERSION",
    "EVENT_WINDOW",
    "EventClusterService",
    "normalize_title",
]
