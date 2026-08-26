from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from config.settings import settings
from database.db import open_database
from trading.research.sentiment.v3.schemas import (
    DragonTigerSnapshot,
    Finality,
    MarginFinancingSnapshot,
    MarketCapitalSnapshot,
    MarketSentimentSnapshot,
    MarketUniverse,
    RecordSource,
    Snapshot,
    SectorCapitalSnapshot,
    SectorSentimentSnapshot,
    SnapshotType,
    StockCapitalSnapshot,
    StockTradingSentimentSnapshot,
    SpeculationSentimentSnapshot,
)
from trading.research.sentiment.v3.text_models import (
    EventIntelligenceRecord,
    EventIntelligenceSnapshot,
    EventReactionSnapshot,
    NewsSentimentSnapshot,
    TextAnalysisRecord,
)


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    snapshot_type VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    symbol_scope VARCHAR NOT NULL,
    source VARCHAR NOT NULL,
    source_timestamp TIMESTAMPTZ NOT NULL,
    available_time TIMESTAMPTZ NOT NULL,
    data_cutoff TIMESTAMPTZ NOT NULL,
    snapshot_time TIMESTAMPTZ NOT NULL,
    finality VARCHAR NOT NULL,
    data_version VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    algorithm_version VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL,
    coverage_ratio DOUBLE NOT NULL,
    quality_flags_json JSON NOT NULL,
    missing_fields_json JSON NOT NULL,
    record_source VARCHAR NOT NULL,
    revision INTEGER NOT NULL,
    corrects_snapshot_id VARCHAR,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (
        snapshot_type, trade_date, symbol_scope, source, record_source,
        schema_version, algorithm_version, content_hash
    )
)
"""


_CREATE_TEXT_ANALYSIS_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_text_analyses (
    cache_key VARCHAR PRIMARY KEY,
    content_hash VARCHAR NOT NULL,
    document_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    model_name VARCHAR NOT NULL,
    prompt_version VARCHAR NOT NULL,
    schema_version VARCHAR NOT NULL,
    algorithm_version VARCHAR NOT NULL,
    available_time TIMESTAMPTZ NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL,
    payload_json JSON NOT NULL
)
"""

_CREATE_EVENT_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_events (
    event_id VARCHAR PRIMARY KEY,
    canonical_event_id VARCHAR NOT NULL,
    symbol VARCHAR NOT NULL,
    record_source VARCHAR NOT NULL DEFAULT 'REAL',
    available_time TIMESTAMPTZ NOT NULL,
    revision INTEGER NOT NULL,
    supersedes_event_id VARCHAR,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(canonical_event_id, record_source, revision)
)
"""

_CREATE_FEATURE_PACKET_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_feature_packets (
    packet_id VARCHAR PRIMARY KEY,
    symbol VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    cutoff TIMESTAMPTZ NOT NULL,
    packet_type VARCHAR NOT NULL,
    record_source VARCHAR NOT NULL,
    feature_registry_version VARCHAR NOT NULL,
    assembly_algorithm_version VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL,
    quality_status VARCHAR NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(symbol, cutoff, packet_type, record_source, content_hash)
)
"""

_CREATE_RESEARCH_DATASET_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_research_datasets (
    dataset_id VARCHAR PRIMARY KEY,
    dataset_version VARCHAR NOT NULL,
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    cutoff_policy VARCHAR NOT NULL,
    record_source VARCHAR NOT NULL,
    content_hash VARCHAR NOT NULL UNIQUE,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
)
"""

_CREATE_UNIVERSE_TABLE = """
CREATE TABLE IF NOT EXISTS sentiment_v3_universes (
    universe_id VARCHAR PRIMARY KEY,
    universe_version VARCHAR NOT NULL UNIQUE,
    universe_scope VARCHAR NOT NULL,
    trade_date DATE NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    source VARCHAR NOT NULL,
    rules_version VARCHAR NOT NULL,
    symbol_hash VARCHAR NOT NULL,
    payload_json JSON NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
)
"""


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


class SentimentV3Repository:
    """Independent DuckDB boundary for research-only Sentiment V3 snapshots."""

    def __init__(self, database_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path or settings.opc_database_path)
        with self._connection() as connection:
            connection.execute(_CREATE_TABLE)
            connection.execute(_CREATE_UNIVERSE_TABLE)
            connection.execute(_CREATE_TEXT_ANALYSIS_TABLE)
            connection.execute(_CREATE_EVENT_TABLE)
            connection.execute(
                "ALTER TABLE sentiment_v3_events ADD COLUMN IF NOT EXISTS "
                "record_source VARCHAR DEFAULT 'REAL'"
            )
            connection.execute(_CREATE_FEATURE_PACKET_TABLE)
            connection.execute(_CREATE_RESEARCH_DATASET_TABLE)

    def _connection(self):
        return open_database(self.database_path)

    @staticmethod
    def _from_payload(payload: str | dict[str, Any]) -> Snapshot:
        value = json.loads(payload) if isinstance(payload, str) else payload
        snapshot_type = value["meta"]["snapshot_type"]
        if snapshot_type == SnapshotType.MARKET.value:
            return MarketSentimentSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.SPECULATION.value:
            return SpeculationSentimentSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.SECTOR.value:
            return SectorSentimentSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.STOCK.value:
            return StockTradingSentimentSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.MARKET_CAPITAL.value:
            return MarketCapitalSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.SECTOR_CAPITAL.value:
            return SectorCapitalSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.STOCK_CAPITAL.value:
            return StockCapitalSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.MARGIN_FINANCING.value:
            return MarginFinancingSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.DRAGON_TIGER.value:
            return DragonTigerSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.NEWS.value:
            return NewsSentimentSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.EVENT_INTELLIGENCE.value:
            return EventIntelligenceSnapshot.model_validate(value)
        if snapshot_type == SnapshotType.EVENT_REACTION.value:
            return EventReactionSnapshot.model_validate(value)
        raise ValueError(f"unsupported Sentiment V3 snapshot type: {snapshot_type}")

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM sentiment_v3_snapshots WHERE snapshot_id = ?",
                [snapshot_id],
            ).fetchone()
        return None if row is None else self._from_payload(row[0])

    def save_snapshot(self, snapshot: Snapshot) -> Snapshot:
        meta = snapshot.meta
        if meta.finality == Finality.CORRECTED:
            previous = self.get_snapshot(meta.corrects_snapshot_id or "")
            if previous is None:
                raise ValueError("corrected snapshot target does not exist")
            old = previous.meta
            if (
                old.snapshot_type != meta.snapshot_type
                or old.trade_date != meta.trade_date
                or old.symbol_scope != meta.symbol_scope
                or old.record_source != meta.record_source
            ):
                raise ValueError("correction must stay in the same snapshot lineage")
            if meta.revision != old.revision + 1:
                raise ValueError("correction revision must increment the target by one")
            if meta.available_time < old.available_time:
                raise ValueError("correction cannot be available before its target")

        payload = snapshot.model_dump(mode="json")
        with self._connection() as connection:
            existing = connection.execute(
                """
                SELECT payload_json
                FROM sentiment_v3_snapshots
                WHERE snapshot_type = ?
                  AND trade_date = ?
                  AND symbol_scope = ?
                  AND source = ?
                  AND record_source = ?
                  AND schema_version = ?
                  AND algorithm_version = ?
                  AND content_hash = ?
                LIMIT 1
                """,
                [
                    meta.snapshot_type.value,
                    meta.trade_date,
                    meta.symbol_scope,
                    meta.source,
                    meta.record_source.value,
                    meta.schema_version,
                    meta.algorithm_version,
                    meta.content_hash,
                ],
            ).fetchone()
            if existing is not None:
                return self._from_payload(existing[0])
            connection.execute(
                """
                INSERT INTO sentiment_v3_snapshots (
                    snapshot_id, snapshot_type, trade_date, symbol_scope,
                    source, source_timestamp, available_time, data_cutoff,
                    snapshot_time, finality, data_version, schema_version,
                    algorithm_version, content_hash, coverage_ratio,
                    quality_flags_json, missing_fields_json, record_source,
                    revision, corrects_snapshot_id, payload_json, created_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                [
                    meta.snapshot_id,
                    meta.snapshot_type.value,
                    meta.trade_date,
                    meta.symbol_scope,
                    meta.source,
                    meta.source_timestamp,
                    meta.available_time,
                    meta.data_cutoff,
                    meta.snapshot_time,
                    meta.finality.value,
                    meta.data_version,
                    meta.schema_version,
                    meta.algorithm_version,
                    meta.content_hash,
                    meta.coverage_ratio,
                    _dump([flag.value for flag in meta.quality_flags]),
                    _dump(meta.missing_fields),
                    meta.record_source.value,
                    meta.revision,
                    meta.corrects_snapshot_id,
                    _dump(payload),
                    meta.created_at,
                ],
            )
        persisted = self.get_snapshot(meta.snapshot_id)
        if persisted is None:
            raise RuntimeError("Sentiment V3 snapshot was not persisted")
        return persisted

    def save_snapshots(self, snapshots: Iterable[Snapshot]) -> list[Snapshot]:
        """Persist a full sector/stock batch in one transaction."""
        values = list({item.meta.snapshot_id: item for item in snapshots}.values())
        if not values:
            return []
        if any(item.meta.finality == Finality.CORRECTED for item in values):
            return [self.save_snapshot(item) for item in values]
        rows = []
        for snapshot in values:
            meta = snapshot.meta
            rows.append(
                [
                    meta.snapshot_id,
                    meta.snapshot_type.value,
                    meta.trade_date,
                    meta.symbol_scope,
                    meta.source,
                    meta.source_timestamp,
                    meta.available_time,
                    meta.data_cutoff,
                    meta.snapshot_time,
                    meta.finality.value,
                    meta.data_version,
                    meta.schema_version,
                    meta.algorithm_version,
                    meta.content_hash,
                    meta.coverage_ratio,
                    _dump([flag.value for flag in meta.quality_flags]),
                    _dump(meta.missing_fields),
                    meta.record_source.value,
                    meta.revision,
                    meta.corrects_snapshot_id,
                    _dump(snapshot.model_dump(mode="json")),
                    meta.created_at,
                ]
            )
        with self._connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                import pandas as pd

                columns = [
                    "snapshot_id",
                    "snapshot_type",
                    "trade_date",
                    "symbol_scope",
                    "source",
                    "source_timestamp",
                    "available_time",
                    "data_cutoff",
                    "snapshot_time",
                    "finality",
                    "data_version",
                    "schema_version",
                    "algorithm_version",
                    "content_hash",
                    "coverage_ratio",
                    "quality_flags_json",
                    "missing_fields_json",
                    "record_source",
                    "revision",
                    "corrects_snapshot_id",
                    "payload_json",
                    "created_at",
                ]
                connection.register("_sentiment_v3_batch", pd.DataFrame(rows, columns=columns))
                connection.execute(
                    """
                    INSERT INTO sentiment_v3_snapshots (
                        snapshot_id, snapshot_type, trade_date, symbol_scope,
                        source, source_timestamp, available_time, data_cutoff,
                        snapshot_time, finality, data_version, schema_version,
                        algorithm_version, content_hash, coverage_ratio,
                        quality_flags_json, missing_fields_json, record_source,
                        revision, corrects_snapshot_id, payload_json, created_at
                    )
                    SELECT * FROM _sentiment_v3_batch
                    ON CONFLICT DO NOTHING
                    """
                )
                connection.unregister("_sentiment_v3_batch")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return values

    def save_universe(self, universe: MarketUniverse) -> MarketUniverse:
        payload = _dump(universe.model_dump(mode="json"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_v3_universes (
                    universe_id, universe_version, universe_scope, trade_date,
                    as_of, source, rules_version, symbol_hash, payload_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    universe.universe_id,
                    universe.universe_version,
                    universe.universe_scope.value,
                    universe.trade_date,
                    universe.as_of,
                    universe.source,
                    universe.rules_version,
                    universe.symbol_hash,
                    payload,
                    universe.created_at,
                ],
            )
            row = connection.execute(
                "SELECT payload_json FROM sentiment_v3_universes WHERE universe_id = ?",
                [universe.universe_id],
            ).fetchone()
        if row is None:
            raise RuntimeError("Sentiment V3 universe was not persisted")
        return MarketUniverse.model_validate(json.loads(row[0]))

    def get_universe_as_of(
        self,
        *,
        cutoff: datetime,
        scope: str | None = None,
    ) -> MarketUniverse | None:
        clauses = ["as_of <= ?"]
        parameters: list[Any] = [cutoff]
        if scope is not None:
            clauses.append("universe_scope = ?")
            parameters.append(scope)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json FROM sentiment_v3_universes
                WHERE {" AND ".join(clauses)}
                ORDER BY as_of DESC, created_at DESC, universe_id DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else MarketUniverse.model_validate(json.loads(row[0]))

    @staticmethod
    def _finality_clause(finalities: Iterable[Finality] | None) -> tuple[str, list[str]]:
        if finalities is None:
            return "", []
        values = [value.value for value in finalities]
        if not values:
            raise ValueError("finalities must not be empty")
        placeholders = ",".join("?" for _ in values)
        return f" AND finality IN ({placeholders})", values

    def get_snapshot_as_of(
        self,
        *,
        snapshot_type: SnapshotType,
        cutoff: datetime,
        symbol_scope: str = "ALL_A",
        record_source: RecordSource = RecordSource.REAL,
        finalities: Iterable[Finality] | None = None,
    ) -> Snapshot | None:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must include a timezone")
        finality_sql, finality_values = self._finality_clause(finalities)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM sentiment_v3_snapshots
                WHERE snapshot_type = ?
                  AND symbol_scope = ?
                  AND record_source = ?
                  AND available_time <= ?
                  AND data_cutoff <= ?
                  {finality_sql}
                ORDER BY available_time DESC, source_timestamp DESC,
                         revision DESC, snapshot_time DESC, snapshot_id DESC
                LIMIT 1
                """,
                [
                    snapshot_type.value,
                    symbol_scope,
                    record_source.value,
                    cutoff,
                    cutoff,
                    *finality_values,
                ],
            ).fetchone()
        return None if row is None else self._from_payload(row[0])

    def get_latest_snapshot(
        self,
        *,
        snapshot_type: SnapshotType,
        symbol_scope: str = "ALL_A",
        record_source: RecordSource = RecordSource.REAL,
        finalities: Iterable[Finality] | None = None,
    ) -> Snapshot | None:
        finality_sql, finality_values = self._finality_clause(finalities)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM sentiment_v3_snapshots
                WHERE snapshot_type = ?
                  AND symbol_scope = ?
                  AND record_source = ?
                  {finality_sql}
                ORDER BY available_time DESC, source_timestamp DESC,
                         revision DESC, snapshot_time DESC, snapshot_id DESC
                LIMIT 1
                """,
                [
                    snapshot_type.value,
                    symbol_scope,
                    record_source.value,
                    *finality_values,
                ],
            ).fetchone()
        return None if row is None else self._from_payload(row[0])

    def previous_final_snapshot(
        self,
        *,
        snapshot_type: SnapshotType,
        before_trade_date: date,
        cutoff: datetime,
        symbol_scope: str = "ALL_A",
        record_source: RecordSource = RecordSource.REAL,
    ) -> Snapshot | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM sentiment_v3_snapshots
                WHERE snapshot_type = ?
                  AND symbol_scope = ?
                  AND record_source = ?
                  AND trade_date < ?
                  AND available_time <= ?
                  AND data_cutoff <= ?
                  AND finality IN ('FINAL', 'CORRECTED')
                ORDER BY trade_date DESC, available_time DESC,
                         revision DESC, snapshot_time DESC
                LIMIT 1
                """,
                [
                    snapshot_type.value,
                    symbol_scope,
                    record_source.value,
                    before_trade_date,
                    cutoff,
                    cutoff,
                ],
            ).fetchone()
        return None if row is None else self._from_payload(row[0])

    def list_snapshots(
        self,
        *,
        snapshot_type: SnapshotType | None = None,
        symbol_scope: str = "ALL_A",
        record_source: RecordSource = RecordSource.REAL,
        finalities: Iterable[Finality] | None = None,
        limit: int = 100,
    ) -> list[Snapshot]:
        if limit < 1:
            raise ValueError("limit must be positive")
        clauses = ["symbol_scope = ?", "record_source = ?"]
        parameters: list[Any] = [symbol_scope, record_source.value]
        if snapshot_type is not None:
            clauses.append("snapshot_type = ?")
            parameters.append(snapshot_type.value)
        finality_sql, finality_values = self._finality_clause(finalities)
        parameters.extend(finality_values)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM sentiment_v3_snapshots
                WHERE {" AND ".join(clauses)}
                {finality_sql}
                ORDER BY available_time DESC, revision DESC, snapshot_id DESC
                LIMIT ?
                """,
                [*parameters, limit],
            ).fetchall()
        return [self._from_payload(row[0]) for row in rows]

    def load_snapshots_as_of(
        self,
        *,
        cutoff: datetime,
        record_source: RecordSource = RecordSource.REAL,
        snapshot_types: Iterable[SnapshotType] | None = None,
        symbol_scopes: Iterable[str] | None = None,
        finalities: Iterable[Finality] | None = None,
    ) -> list[Snapshot]:
        """Bulk PIT load, returning one latest revision per type and scope."""
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must include a timezone")
        clauses = ["record_source = ?", "available_time <= ?", "data_cutoff <= ?"]
        parameters: list[Any] = [record_source.value, cutoff, cutoff]
        if snapshot_types is not None:
            values = [item.value for item in snapshot_types]
            if not values:
                return []
            clauses.append(f"snapshot_type IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
        if symbol_scopes is not None:
            scopes = sorted(set(symbol_scopes))
            if not scopes:
                return []
            clauses.append(f"symbol_scope IN ({','.join('?' for _ in scopes)})")
            parameters.extend(scopes)
        if finalities is not None:
            values = [item.value for item in finalities]
            if not values:
                return []
            clauses.append(f"finality IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM sentiment_v3_snapshots
                WHERE {" AND ".join(clauses)}
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY snapshot_type, symbol_scope
                    ORDER BY available_time DESC, source_timestamp DESC,
                             revision DESC, snapshot_time DESC, snapshot_id DESC
                ) = 1
                """,
                parameters,
            ).fetchall()
        return [self._from_payload(row[0]) for row in rows]

    def list_events_as_of(
        self,
        *,
        cutoff: datetime,
        symbols: Iterable[str],
        record_source: RecordSource = RecordSource.REAL,
    ) -> list[EventIntelligenceRecord]:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must include a timezone")
        values = sorted(set(symbols))
        if not values:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json FROM sentiment_v3_events
                WHERE available_time <= ?
                  AND record_source = ?
                  AND symbol IN ({",".join("?" for _ in values)})
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY canonical_event_id
                    ORDER BY available_time DESC, revision DESC, created_at DESC
                ) = 1
                """,
                [cutoff, record_source.value, *values],
            ).fetchall()
        return [EventIntelligenceRecord.model_validate(json.loads(row[0])) for row in rows]

    def list_event_reactions_as_of(
        self,
        *,
        cutoff: datetime,
        symbols: Iterable[str],
        record_source: RecordSource = RecordSource.REAL,
    ) -> list[EventReactionSnapshot]:
        values = sorted(set(symbols))
        if not values:
            return []
        with self._connection() as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json FROM sentiment_v3_snapshots
                WHERE snapshot_type = ? AND record_source = ?
                  AND available_time <= ? AND data_cutoff <= ?
                  AND symbol_scope IN ({",".join("?" for _ in values)})
                ORDER BY available_time DESC, revision DESC, snapshot_time DESC
                """,
                [SnapshotType.EVENT_REACTION.value, record_source.value, cutoff, cutoff, *values],
            ).fetchall()
        result: list[EventReactionSnapshot] = []
        seen: set[str] = set()
        for row in rows:
            value = EventReactionSnapshot.model_validate(json.loads(row[0]))
            if value.event_id not in seen:
                seen.add(value.event_id)
                result.append(value)
        return result

    def save_feature_packets(self, packets: Iterable[Any]) -> list[Any]:
        values = list({item.packet_id: item for item in packets}.values())
        if not values:
            return []
        import pandas as pd

        rows = [
            [
                item.packet_id,
                item.symbol,
                item.trade_date,
                item.decision_time,
                item.cutoff,
                item.packet_type.value,
                item.record_source.value,
                item.feature_schema_version,
                item.assembly_algorithm_version,
                item.content_hash,
                item.quality_status.value,
                _dump(item.model_dump(mode="json")),
                item.created_at,
            ]
            for item in values
        ]
        columns = [
            "packet_id",
            "symbol",
            "trade_date",
            "decision_time",
            "cutoff",
            "packet_type",
            "record_source",
            "feature_registry_version",
            "assembly_algorithm_version",
            "content_hash",
            "quality_status",
            "payload_json",
            "created_at",
        ]
        with self._connection() as connection:
            connection.register("_sentiment_v3_packet_batch", pd.DataFrame(rows, columns=columns))
            try:
                connection.execute(
                    """
                    INSERT INTO sentiment_v3_feature_packets SELECT *
                    FROM _sentiment_v3_packet_batch ON CONFLICT DO NOTHING
                    """
                )
            finally:
                connection.unregister("_sentiment_v3_packet_batch")
        return values

    def get_feature_packet(self, packet_id: str) -> Any | None:
        from trading.research.sentiment.v3.feature_assembly import ResearchFeaturePacket

        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM sentiment_v3_feature_packets WHERE packet_id = ?",
                [packet_id],
            ).fetchone()
        return None if row is None else ResearchFeaturePacket.model_validate(json.loads(row[0]))

    def list_feature_packets(
        self,
        *,
        record_source: RecordSource = RecordSource.REAL,
        limit: int = 1000,
    ) -> list[Any]:
        from trading.research.sentiment.v3.feature_assembly import ResearchFeaturePacket

        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT payload_json FROM sentiment_v3_feature_packets
                WHERE record_source = ?
                ORDER BY cutoff DESC, symbol, packet_id LIMIT ?
                """,
                [record_source.value, limit],
            ).fetchall()
        return [ResearchFeaturePacket.model_validate(json.loads(row[0])) for row in rows]

    def save_research_dataset(self, dataset: Any) -> Any:
        payload = _dump(dataset.model_dump(mode="json"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_v3_research_datasets (
                    dataset_id, dataset_version, date_from, date_to, cutoff_policy,
                    record_source, content_hash, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
                """,
                [
                    dataset.dataset_id,
                    dataset.dataset_version,
                    dataset.date_from,
                    dataset.date_to,
                    dataset.cutoff_policy.value,
                    dataset.record_source.value,
                    dataset.content_hash,
                    payload,
                    dataset.created_at,
                ],
            )
        return dataset

    def shadow_stats(self) -> dict[str, Any]:
        with self._connection() as connection:
            sources = connection.execute(
                """
                SELECT record_source, COUNT(*) FROM sentiment_v3_feature_packets
                GROUP BY record_source ORDER BY record_source
                """
            ).fetchall()
            qualities = connection.execute(
                """
                SELECT quality_status, COUNT(*) FROM sentiment_v3_feature_packets
                GROUP BY quality_status ORDER BY quality_status
                """
            ).fetchall()
        return {
            "record_source_counts": {row[0]: row[1] for row in sources},
            "quality_status_counts": {row[0]: row[1] for row in qualities},
        }

    def get_news_analysis_by_hash(self, *, cache_key: str) -> TextAnalysisRecord | None:
        started = datetime.now().astimezone()
        del started
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM sentiment_v3_text_analyses WHERE cache_key = ?",
                [cache_key],
            ).fetchone()
        return None if row is None else TextAnalysisRecord.model_validate(json.loads(row[0]))

    def save_news_analysis(self, analysis: TextAnalysisRecord) -> TextAnalysisRecord:
        payload = _dump(analysis.model_dump(mode="json"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_v3_text_analyses (
                    cache_key, content_hash, document_id, symbol, provider,
                    model_name, prompt_version, schema_version, algorithm_version,
                    available_time, processed_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    analysis.cache_key,
                    analysis.content_hash,
                    analysis.document_id,
                    analysis.symbol,
                    analysis.provider,
                    analysis.model_name,
                    analysis.prompt_version,
                    analysis.schema_version,
                    analysis.algorithm_version,
                    analysis.output.available_time,
                    analysis.processed_at,
                    payload,
                ],
            )
            row = connection.execute(
                "SELECT payload_json FROM sentiment_v3_text_analyses WHERE cache_key = ?",
                [analysis.cache_key],
            ).fetchone()
        if row is None:
            raise RuntimeError("text analysis was not persisted")
        return TextAnalysisRecord.model_validate(json.loads(row[0]))

    def get_event(self, event_id: str) -> EventIntelligenceRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload_json FROM sentiment_v3_events WHERE event_id = ?", [event_id]
            ).fetchone()
        return None if row is None else EventIntelligenceRecord.model_validate(json.loads(row[0]))

    def save_event(self, event: EventIntelligenceRecord) -> EventIntelligenceRecord:
        with self._connection() as connection:
            latest = connection.execute(
                """
                SELECT payload_json FROM sentiment_v3_events
                WHERE canonical_event_id = ? AND record_source = ?
                ORDER BY revision DESC LIMIT 1
                """,
                [event.canonical_event_id, event.record_source.value],
            ).fetchone()
        if latest is not None:
            previous = EventIntelligenceRecord.model_validate(json.loads(latest[0]))
            if previous.source_document_hashes == event.source_document_hashes:
                return previous
            merged_hashes = sorted(
                set(previous.source_document_hashes) | set(event.source_document_hashes)
            )
            event = event.model_copy(
                update={
                    "revision": previous.revision + 1,
                    "supersedes_event_id": previous.event_id,
                    "source_document_hashes": merged_hashes,
                    "official_confirmation_exists": (
                        previous.official_confirmation_exists or event.official_confirmation_exists
                    ),
                }
            )
        payload = _dump(event.model_dump(mode="json"))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO sentiment_v3_events (
                    event_id, canonical_event_id, symbol, record_source, available_time,
                    revision, supersedes_event_id, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    event.event_id,
                    event.canonical_event_id,
                    event.symbol,
                    event.record_source.value,
                    event.available_time,
                    event.revision,
                    event.supersedes_event_id,
                    payload,
                    event.processed_at,
                ],
            )
        persisted = self.get_event(event.event_id)
        if persisted is None:
            raise RuntimeError("event was not persisted")
        return persisted

    def get_event_as_of(
        self,
        *,
        cutoff: datetime,
        canonical_event_id: str | None = None,
        symbol: str | None = None,
    ) -> EventIntelligenceRecord | None:
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("cutoff must include a timezone")
        clauses = ["available_time <= ?"]
        parameters: list[Any] = [cutoff]
        if canonical_event_id is not None:
            clauses.append("canonical_event_id = ?")
            parameters.append(canonical_event_id)
        if symbol is not None:
            clauses.append("symbol = ?")
            parameters.append(symbol)
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT payload_json FROM sentiment_v3_events
                WHERE {" AND ".join(clauses)}
                ORDER BY available_time DESC, revision DESC, created_at DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else EventIntelligenceRecord.model_validate(json.loads(row[0]))

    def save_event_reaction(self, reaction: EventReactionSnapshot) -> EventReactionSnapshot:
        saved = self.save_snapshot(reaction)
        if not isinstance(saved, EventReactionSnapshot):
            raise TypeError("saved snapshot is not EventReactionSnapshot")
        return saved

    def get_event_reaction_as_of(
        self,
        *,
        cutoff: datetime,
        symbol: str,
        record_source: RecordSource = RecordSource.REAL,
    ) -> EventReactionSnapshot | None:
        value = self.get_snapshot_as_of(
            snapshot_type=SnapshotType.EVENT_REACTION,
            cutoff=cutoff,
            symbol_scope=symbol,
            record_source=record_source,
        )
        if value is None:
            return None
        if not isinstance(value, EventReactionSnapshot):
            raise TypeError("repository returned unexpected snapshot type")
        return value


__all__ = ["SentimentV3Repository"]
