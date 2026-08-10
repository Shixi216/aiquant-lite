from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import pandas as pd

from database.db import get_connection, initialize_database
from data_hub.schemas.full_market import (
    CandidateEnrichmentItem,
    DataCoverageResponse,
    IndustryMembership,
    ListingStatus,
    MarketSnapshotItem,
    ProviderCapability,
    SnapshotCompleteness,
    StockAlias,
    StockUniverseRecord,
    VerificationStatus,
)
from data_hub.schemas.market import MarketRecord


def _dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _load(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _bulk_insert_ignore(
    connection: Any,
    *,
    table: str,
    columns: list[str],
    rows: list[list[Any]],
) -> None:
    """Use one set-based DuckDB insert instead of slow Python row execution."""
    if not rows:
        return
    view_name = "_hermes_bulk_rows"
    frame = pd.DataFrame.from_records(rows, columns=columns)
    quoted_columns = ", ".join(f'"{column}"' for column in columns)
    connection.register(view_name, frame)
    try:
        connection.execute(
            f"""
            INSERT INTO "{table}" ({quoted_columns})
            SELECT {quoted_columns} FROM "{view_name}"
            ON CONFLICT DO NOTHING
            """
        )
    finally:
        connection.unregister(view_name)


class FullMarketRepository:
    """Batched persistence for the full-market data foundation."""

    def __init__(self) -> None:
        initialize_database()

    @staticmethod
    def _stock(row: tuple[Any, ...]) -> StockUniverseRecord:
        return StockUniverseRecord(
            symbol=row[0],
            exchange=row[1],
            market=row[2],
            board=row[3],
            security_type=row[4],
            company_name=row[5],
            short_name=row[6],
            list_date=row[7],
            delist_date=row[8],
            listing_status=ListingStatus(row[9]),
            is_st=row[10],
            is_suspended=row[11],
            currency=row[12],
            price_limit_type=row[13],
            primary_source=row[14],
            source_record_ids=_load(row[15]),
            verification_status=VerificationStatus(row[16]),
            source_differences=_load(row[17]),
            data_available_time=row[18],
            updated_at=row[19],
            universe_version=row[20],
        )

    @staticmethod
    def _stock_columns() -> str:
        return """
            symbol, exchange, market, board, security_type,
            company_name, short_name, list_date, delist_date,
            listing_status, is_st, is_suspended, currency,
            price_limit_type, primary_source, source_record_ids_json,
            verification_status, source_differences_json,
            data_available_time, updated_at, universe_version
        """

    def latest_universe_version(self, data_cutoff: datetime | None = None) -> str | None:
        clause = "WHERE data_cutoff <= ?" if data_cutoff is not None else ""
        parameters = [data_cutoff] if data_cutoff is not None else []
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT universe_version
                FROM stock_universe_versions
                {clause}
                ORDER BY effective_at DESC, universe_version DESC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
        return None if row is None else str(row[0])

    def universe_count(
        self,
        *,
        version: str | None = None,
        active_only: bool = False,
    ) -> int:
        actual_version = version or self.latest_universe_version()
        if actual_version is None:
            return 0
        clauses = ["universe_version = ?"]
        parameters: list[Any] = [actual_version]
        if active_only:
            clauses.append("listing_status = 'ACTIVE'")
        with get_connection() as connection:
            return int(
                connection.execute(
                    f"""
                    SELECT COUNT(*) FROM stock_universe
                    WHERE {' AND '.join(clauses)}
                    """,
                    parameters,
                ).fetchone()[0]
            )

    def list_universe(
        self,
        *,
        version: str | None = None,
        active_only: bool = False,
        exchange: str | None = None,
        board: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[str | None, int, list[StockUniverseRecord]]:
        actual_version = version or self.latest_universe_version()
        if actual_version is None:
            return None, 0, []
        clauses = ["universe_version = ?"]
        parameters: list[Any] = [actual_version]
        if active_only:
            clauses.append("listing_status = 'ACTIVE'")
        if exchange:
            clauses.append("exchange = ?")
            parameters.append(exchange.upper())
        if board:
            clauses.append("board = ?")
            parameters.append(board.upper())
        where = " AND ".join(clauses)
        with get_connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM stock_universe WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT {self._stock_columns()}
                FROM stock_universe
                WHERE {where}
                ORDER BY symbol
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
        return actual_version, total, [self._stock(row) for row in rows]

    def get_stock(
        self,
        symbol: str,
        *,
        version: str | None = None,
        data_cutoff: datetime | None = None,
    ) -> StockUniverseRecord | None:
        actual_version = version or self.latest_universe_version(data_cutoff)
        if actual_version is None:
            return None
        with get_connection() as connection:
            row = connection.execute(
                f"""
                SELECT {self._stock_columns()}
                FROM stock_universe
                WHERE symbol = ? AND universe_version = ?
                """,
                [symbol, actual_version],
            ).fetchone()
        return None if row is None else self._stock(row)

    def aliases(
        self,
        *,
        version: str | None = None,
        data_cutoff: datetime | None = None,
    ) -> list[StockAlias]:
        actual_version = version or self.latest_universe_version(data_cutoff)
        if actual_version is None:
            return []
        clauses = ["universe_version = ?"]
        parameters: list[Any] = [actual_version]
        if data_cutoff is not None:
            clauses.extend(
                [
                    "(valid_from IS NULL OR valid_from <= ?)",
                    "(valid_to IS NULL OR valid_to >= ?)",
                ]
            )
            parameters.extend([data_cutoff, data_cutoff])
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT alias_id, symbol, alias_name, alias_type,
                       valid_from, valid_to, source, verification_status,
                       normalized_alias, generated_at, universe_version
                FROM stock_aliases
                WHERE {' AND '.join(clauses)}
                ORDER BY normalized_alias, symbol
                """,
                parameters,
            ).fetchall()
        return [
            StockAlias(
                alias_id=row[0],
                symbol=row[1],
                alias_name=row[2],
                alias_type=row[3],
                valid_from=row[4],
                valid_to=row[5],
                source=row[6],
                verification_status=row[7],
                normalized_alias=row[8],
                generated_at=row[9],
                universe_version=row[10],
            )
            for row in rows
        ]

    def industry_symbols(self, data_cutoff: datetime) -> set[str]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT symbol
                FROM stock_industry_memberships
                WHERE (valid_from IS NULL OR valid_from <= ?)
                  AND (valid_to IS NULL OR valid_to >= ?)
                """,
                [data_cutoff, data_cutoff],
            ).fetchall()
        return {str(row[0]) for row in rows}

    def save_universe(
        self,
        *,
        run_id: str,
        provider: str,
        request_budget: int,
        request_count: int,
        data_cutoff: datetime,
        started_at: datetime,
        completed_at: datetime,
        universe_version: str,
        content_hash: str,
        records: list[StockUniverseRecord],
        aliases: list[StockAlias],
        industries: list[IndustryMembership],
        raw_records: list[MarketRecord],
        conflict_count: int,
        failed_count: int,
        warnings: list[str],
        report_path: str | None = None,
    ) -> int:
        del request_count
        with get_connection() as connection:
            existing_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM stock_universe
                    WHERE universe_version = ?
                    """,
                    [universe_version],
                ).fetchone()[0]
            )
            persisted_count = max(0, len(records) - existing_count)
            connection.execute("BEGIN TRANSACTION")
            try:
                _bulk_insert_ignore(
                    connection,
                    table="data_records",
                    columns=[
                        "record_id",
                        "task_id",
                        "symbol",
                        "data_type",
                        "event_time",
                        "fetched_at",
                        "source_name",
                        "source_url",
                        "source_level",
                        "verified",
                        "content_hash",
                        "payload_json",
                    ],
                    rows=[
                        [
                            raw.record_id,
                            None,
                            raw.symbol,
                            raw.data_type.value,
                            raw.event_time,
                            raw.fetched_at,
                            raw.source_name,
                            str(raw.source_url) if raw.source_url else None,
                            raw.source_level.value,
                            raw.verified,
                            raw.content_hash,
                            _dump(raw.data),
                        ]
                        for raw in raw_records
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO stock_universe_versions (
                        universe_version, content_hash, effective_at,
                        data_cutoff, generated_at, total_count, active_count,
                        source_names_json, metadata_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (universe_version) DO NOTHING
                    """,
                    [
                        universe_version,
                        content_hash,
                        data_cutoff,
                        data_cutoff,
                        completed_at,
                        len(records),
                        sum(
                            item.listing_status == ListingStatus.ACTIVE
                            for item in records
                        ),
                        _dump(sorted({item.primary_source for item in records})),
                        _dump({"warnings": warnings}),
                    ],
                )
                _bulk_insert_ignore(
                    connection,
                    table="stock_universe",
                    columns=[
                        "symbol",
                        "exchange",
                        "market",
                        "board",
                        "security_type",
                        "company_name",
                        "short_name",
                        "list_date",
                        "delist_date",
                        "listing_status",
                        "is_st",
                        "is_suspended",
                        "currency",
                        "price_limit_type",
                        "primary_source",
                        "source_record_ids_json",
                        "verification_status",
                        "source_differences_json",
                        "data_available_time",
                        "updated_at",
                        "universe_version",
                    ],
                    rows=[
                        [
                            item.symbol,
                            item.exchange,
                            item.market,
                            item.board,
                            item.security_type,
                            item.company_name,
                            item.short_name,
                            item.list_date,
                            item.delist_date,
                            item.listing_status.value,
                            item.is_st,
                            item.is_suspended,
                            item.currency,
                            item.price_limit_type,
                            item.primary_source,
                            _dump(item.source_record_ids),
                            item.verification_status.value,
                            _dump(item.source_differences),
                            item.data_available_time,
                            item.updated_at,
                            item.universe_version,
                        ]
                        for item in records
                    ],
                )
                _bulk_insert_ignore(
                    connection,
                    table="stock_aliases",
                    columns=[
                        "alias_id",
                        "symbol",
                        "alias_name",
                        "alias_type",
                        "valid_from",
                        "valid_to",
                        "source",
                        "verification_status",
                        "normalized_alias",
                        "generated_at",
                        "universe_version",
                    ],
                    rows=[
                        [
                            item.alias_id,
                            item.symbol,
                            item.alias_name,
                            item.alias_type.value,
                            item.valid_from,
                            item.valid_to,
                            item.source,
                            item.verification_status.value,
                            item.normalized_alias,
                            item.generated_at,
                            item.universe_version,
                        ]
                        for item in aliases
                    ],
                )
                _bulk_insert_ignore(
                    connection,
                    table="stock_industry_memberships",
                    columns=[
                        "membership_id",
                        "symbol",
                        "industry_code",
                        "industry_name",
                        "industry_level",
                        "classification_system",
                        "valid_from",
                        "valid_to",
                        "source",
                        "verification_status",
                        "mapping_version",
                        "generated_at",
                    ],
                    rows=[
                        [
                            item.membership_id,
                            item.symbol,
                            item.industry_code,
                            item.industry_name,
                            item.industry_level,
                            item.classification_system,
                            item.valid_from,
                            item.valid_to,
                            item.source,
                            item.verification_status.value,
                            item.mapping_version,
                            item.generated_at,
                        ]
                        for item in industries
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO universe_sync_runs (
                        run_id, mode, provider, request_budget, data_cutoff,
                        status, expected_count, received_count,
                        persisted_count, conflict_count, failed_count,
                        universe_version, started_at, completed_at,
                        report_path, error_message
                    )
                    VALUES (
                        ?, 'APPLY', ?, ?, ?, 'SUCCESS', ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, NULL
                    )
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    [
                        run_id,
                        provider,
                        request_budget,
                        data_cutoff,
                        len(records),
                        len(records),
                        persisted_count,
                        conflict_count,
                        failed_count,
                        universe_version,
                        started_at,
                        completed_at,
                        report_path,
                    ],
                )
                _bulk_insert_ignore(
                    connection,
                    table="universe_sync_items",
                    columns=[
                        "run_id",
                        "symbol",
                        "status",
                        "source_record_ids_json",
                        "differences_json",
                        "payload_json",
                        "processed_at",
                        "error_message",
                    ],
                    rows=[
                        [
                            run_id,
                            item.symbol,
                            item.verification_status.value,
                            _dump(item.source_record_ids),
                            _dump(item.source_differences),
                            _dump(item.model_dump(mode="json")),
                            completed_at,
                            None,
                        ]
                        for item in records
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return persisted_count

    def save_capabilities(self, values: list[ProviderCapability]) -> None:
        if not values:
            return
        with get_connection() as connection:
            connection.executemany(
                """
                INSERT INTO provider_capabilities (
                    provider, capability, available, verified_at,
                    failure_reason, batch_supported, maximum_batch_size,
                    rate_limit, requires_permission, fallback_provider,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                [
                    [
                        item.provider,
                        item.capability,
                        item.available,
                        item.verified_at,
                        item.failure_reason,
                        item.batch_supported,
                        item.maximum_batch_size,
                        item.rate_limit,
                        item.requires_permission,
                        item.fallback_provider,
                        _dump(item.metadata),
                    ]
                    for item in values
                ],
            )

    def save_raw_records(self, records: list[MarketRecord]) -> int:
        if not records:
            return 0
        with get_connection() as connection:
            before = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM data_records
                    WHERE record_id IN (
                        SELECT UNNEST(?::VARCHAR[])
                    )
                    """,
                    [[item.record_id for item in records]],
                ).fetchone()[0]
            )
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.executemany(
                    """
                    INSERT INTO data_records (
                        record_id, task_id, symbol, data_type, event_time,
                        fetched_at, source_name, source_url, source_level,
                        verified, content_hash, payload_json
                    )
                    VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (record_id) DO NOTHING
                    """,
                    [
                        [
                            item.record_id,
                            item.symbol,
                            item.data_type.value,
                            item.event_time,
                            item.fetched_at,
                            item.source_name,
                            str(item.source_url) if item.source_url else None,
                            item.source_level.value,
                            item.verified,
                            item.content_hash,
                            _dump(item.data),
                        ]
                        for item in records
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
        return max(0, len(records) - before)

    def save_market_snapshot(
        self,
        *,
        snapshot_id: str,
        provider: str,
        data_cutoff: datetime,
        expected_count: int,
        received_count: int,
        valid_count: int,
        coverage_ratio: float,
        completeness: SnapshotCompleteness,
        started_at: datetime,
        completed_at: datetime,
        snapshot_time: datetime,
        content_hash: str,
        request_count: int,
        elapsed_seconds: float,
        peak_memory_bytes: int | None,
        database_growth_bytes: int | None,
        items: list[MarketSnapshotItem],
        raw_records: list[MarketRecord],
    ) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                _bulk_insert_ignore(
                    connection,
                    table="data_records",
                    columns=[
                        "record_id",
                        "task_id",
                        "symbol",
                        "data_type",
                        "event_time",
                        "fetched_at",
                        "source_name",
                        "source_url",
                        "source_level",
                        "verified",
                        "content_hash",
                        "payload_json",
                    ],
                    rows=[
                        [
                            raw.record_id,
                            None,
                            raw.symbol,
                            raw.data_type.value,
                            raw.event_time,
                            raw.fetched_at,
                            raw.source_name,
                            None,
                            raw.source_level.value,
                            raw.verified,
                            raw.content_hash,
                            _dump(raw.data),
                        ]
                        for raw in raw_records
                    ],
                )
                connection.execute(
                    """
                    INSERT INTO market_snapshot_runs (
                        snapshot_id, mode, provider, data_cutoff,
                        expected_universe_size, received_symbol_count,
                        valid_symbol_count, missing_symbol_count,
                        coverage_ratio, completeness_status, started_at,
                        completed_at, snapshot_time, content_hash,
                        request_count, elapsed_seconds, peak_memory_bytes,
                        database_growth_bytes, error_message
                    )
                    VALUES (
                        ?, 'APPLY', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, NULL
                    )
                    ON CONFLICT (snapshot_id) DO NOTHING
                    """,
                    [
                        snapshot_id,
                        provider,
                        data_cutoff,
                        expected_count,
                        received_count,
                        valid_count,
                        max(0, expected_count - valid_count),
                        coverage_ratio,
                        completeness.value,
                        started_at,
                        completed_at,
                        snapshot_time,
                        content_hash,
                        request_count,
                        elapsed_seconds,
                        peak_memory_bytes,
                        database_growth_bytes,
                    ],
                )
                _bulk_insert_ignore(
                    connection,
                    table="market_snapshot_items",
                    columns=[
                        "snapshot_id",
                        "symbol",
                        "price",
                        "previous_close",
                        "open",
                        "high",
                        "low",
                        "volume",
                        "amount",
                        "change",
                        "change_pct",
                        "turnover_rate",
                        "snapshot_time",
                        "source",
                        "item_status",
                        "is_suspended",
                        "is_abnormal",
                        "raw_record_id",
                        "payload_json",
                    ],
                    rows=[
                        [
                            snapshot_id,
                            item.symbol,
                            item.price,
                            item.previous_close,
                            item.open,
                            item.high,
                            item.low,
                            item.volume,
                            item.amount,
                            item.change,
                            item.change_pct,
                            item.turnover_rate,
                            item.snapshot_time,
                            item.source,
                            item.item_status,
                            item.is_suspended,
                            item.is_abnormal,
                            item.raw_record_id,
                            _dump(item.payload),
                        ]
                        for item in items
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def latest_market_snapshot(
        self,
        *,
        limit: int = 100,
    ) -> tuple[tuple[Any, ...], list[MarketSnapshotItem]] | None:
        with get_connection() as connection:
            run = connection.execute(
                """
                SELECT snapshot_id, mode, provider, data_cutoff,
                       expected_universe_size, received_symbol_count,
                       valid_symbol_count, missing_symbol_count,
                       coverage_ratio, completeness_status, snapshot_time,
                       content_hash, request_count, elapsed_seconds,
                       peak_memory_bytes
                FROM market_snapshot_runs
                WHERE mode = 'APPLY' AND completeness_status != 'FAILED'
                ORDER BY snapshot_time DESC, completed_at DESC
                LIMIT 1
                """
            ).fetchone()
            if run is None:
                return None
            rows = connection.execute(
                """
                SELECT symbol, price, previous_close, open, high, low,
                       volume, amount, change, change_pct, turnover_rate,
                       snapshot_time, source, item_status, is_suspended,
                       is_abnormal, raw_record_id, payload_json
                FROM market_snapshot_items
                WHERE snapshot_id = ?
                ORDER BY symbol
                LIMIT ?
                """,
                [run[0], limit],
            ).fetchall()
        items = [
            MarketSnapshotItem(
                symbol=row[0],
                price=row[1],
                previous_close=row[2],
                open=row[3],
                high=row[4],
                low=row[5],
                volume=row[6],
                amount=row[7],
                change=row[8],
                change_pct=row[9],
                turnover_rate=row[10],
                snapshot_time=row[11],
                source=row[12],
                item_status=row[13],
                is_suspended=row[14],
                is_abnormal=row[15],
                raw_record_id=row[16],
                payload=_load(row[17]),
            )
            for row in rows
        ]
        return run, items

    def save_expansion_run(
        self,
        *,
        run_id: str,
        expansion_type: str,
        mode: str,
        analysis_mode: str,
        provider: str,
        request_budget: int,
        request_count: int,
        data_cutoff: datetime,
        filters: dict[str, Any],
        status: str,
        processed_count: int,
        success_count: int,
        skipped_count: int,
        conflict_count: int,
        failed_count: int,
        started_at: datetime,
        completed_at: datetime,
        report_path: str | None = None,
        error_message: str | None = None,
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO data_expansion_runs (
                    run_id, expansion_type, mode, analysis_mode, provider,
                    request_budget, request_count, data_cutoff, filters_json,
                    status, processed_count, success_count, skipped_count,
                    conflict_count, failed_count, started_at, completed_at,
                    report_path, error_message
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                ON CONFLICT (run_id) DO NOTHING
                """,
                [
                    run_id,
                    expansion_type,
                    mode,
                    analysis_mode,
                    provider,
                    request_budget,
                    request_count,
                    data_cutoff,
                    _dump(filters),
                    status,
                    processed_count,
                    success_count,
                    skipped_count,
                    conflict_count,
                    failed_count,
                    started_at,
                    completed_at,
                    report_path,
                    error_message,
                ],
            )

    def save_expansion_items(
        self,
        *,
        run_id: str,
        items: list[dict[str, Any]],
    ) -> None:
        if not items:
            return
        with get_connection() as connection:
            connection.executemany(
                """
                INSERT INTO data_expansion_items (
                    run_id, item_key, symbol, data_type, status,
                    source_record_ids_json, canonical_record_ids_json,
                    event_cluster_ids_json, request_count, processed_at,
                    error_type, error_message
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (run_id, item_key) DO NOTHING
                """,
                [
                    [
                        run_id,
                        item["item_key"],
                        item.get("symbol"),
                        item["data_type"],
                        item["status"],
                        _dump(item.get("source_record_ids", [])),
                        _dump(item.get("canonical_record_ids", [])),
                        _dump(item.get("event_cluster_ids", [])),
                        item.get("request_count", 0),
                        item["processed_at"],
                        item.get("error_type"),
                        item.get("error_message"),
                    ]
                    for item in items
                ],
            )

    def save_collection_window_audits(
        self,
        audits: list[dict[str, Any]],
    ) -> None:
        if not audits:
            return
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.executemany(
                    """
                    INSERT INTO collection_window_audits (
                        audit_id, run_id, data_kind, provider,
                        window_start, window_end,
                        provider_window_capability, batch_key, status,
                        result_count, full_text_count, metadata_only_count,
                        duplicate_source_count, started_at, completed_at,
                        error_type, error_message, payload_json
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    [
                        [
                            item["audit_id"],
                            item.get("run_id"),
                            item["data_kind"],
                            item["provider"],
                            item["window_start"],
                            item["window_end"],
                            item["provider_window_capability"],
                            item["batch_key"],
                            item["status"],
                            item["result_count"],
                            item["full_text_count"],
                            item["metadata_only_count"],
                            item["duplicate_source_count"],
                            item["started_at"],
                            item["completed_at"],
                            item.get("error_type"),
                            item.get("error_message"),
                            _dump(item.get("payload", {})),
                        ]
                        for item in audits
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def save_entity_links(
        self,
        audits: list[dict[str, Any]],
    ) -> None:
        if not audits:
            return
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.executemany(
                    """
                    INSERT INTO entity_link_audits (
                        audit_id, event_cluster_id, symbol, link_type,
                        matched_text, relevance_weight, confidence,
                        evidence_ids_json, matching_rule, mapping_version,
                        generated_at, manually_confirmed, status,
                        supersedes_audit_id, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (audit_id) DO NOTHING
                    """,
                    [
                        [
                            item["audit_id"],
                            item["event_cluster_id"],
                            item.get("symbol"),
                            item["link_type"],
                            item.get("matched_text"),
                            item["relevance_weight"],
                            item["confidence"],
                            _dump(item["evidence_ids"]),
                            item["matching_rule"],
                            item["mapping_version"],
                            item["generated_at"],
                            item.get("manually_confirmed", False),
                            item["status"],
                            item.get("supersedes_audit_id"),
                            _dump(item.get("payload", {})),
                        ]
                        for item in audits
                    ],
                )
                link_rows = [
                    [item["event_cluster_id"], item["symbol"]]
                    for item in audits
                    if item.get("symbol") and item["status"] == "LINKED"
                ]
                if link_rows:
                    connection.executemany(
                        """
                        INSERT INTO event_symbol_links (event_cluster_id, symbol)
                        VALUES (?, ?)
                        ON CONFLICT DO NOTHING
                        """,
                        link_rows,
                    )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def reverse_entity_link(self, audit: dict[str, Any]) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO entity_link_audits (
                        audit_id, event_cluster_id, symbol, link_type,
                        matched_text, relevance_weight, confidence,
                        evidence_ids_json, matching_rule, mapping_version,
                        generated_at, manually_confirmed, status,
                        supersedes_audit_id, payload_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        audit["audit_id"],
                        audit["event_cluster_id"],
                        audit["symbol"],
                        audit["link_type"],
                        audit.get("matched_text"),
                        audit["relevance_weight"],
                        audit["confidence"],
                        _dump(audit["evidence_ids"]),
                        audit["matching_rule"],
                        audit["mapping_version"],
                        audit["generated_at"],
                        True,
                        "REVERSED",
                        audit["supersedes_audit_id"],
                        _dump(audit.get("payload", {})),
                    ],
                )
                connection.execute(
                    """
                    DELETE FROM event_symbol_links
                    WHERE event_cluster_id = ? AND symbol = ?
                    """,
                    [audit["event_cluster_id"], audit["symbol"]],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def event_link_inputs(
        self,
        *,
        data_cutoff: datetime,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        limit_clause = "LIMIT ?" if limit is not None else ""
        parameters: list[Any] = [data_cutoff]
        if limit is not None:
            parameters.append(limit)
        with get_connection() as connection:
            rows = connection.execute(
                f"""
                SELECT c.event_cluster_id, c.canonical_title, c.event_time,
                       c.primary_source_id, d.payload_json
                FROM event_clusters c
                JOIN data_records d
                  ON d.record_id = c.primary_source_id
                WHERE c.data_cutoff <= ?
                ORDER BY c.event_time, c.event_cluster_id
                {limit_clause}
                """,
                parameters,
            ).fetchall()
        return [
            {
                "event_cluster_id": row[0],
                "title": row[1],
                "event_time": row[2],
                "primary_source_id": row[3],
                "payload": _load(row[4]),
            }
            for row in rows
        ]

    def save_coverage(self, report: DataCoverageResponse, report_path: str | None) -> None:
        payload = report.model_dump(mode="json")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO data_coverage_snapshots (
                    coverage_id, data_cutoff, generated_at,
                    universe_version, payload_json, content_hash, report_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (content_hash) DO NOTHING
                """,
                [
                    report.coverage_id,
                    report.data_cutoff,
                    report.generated_at,
                    report.universe_version,
                    _dump(payload),
                    report.content_hash,
                    report_path,
                ],
            )

    def latest_coverage(self) -> DataCoverageResponse | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM data_coverage_snapshots
                ORDER BY data_cutoff DESC, generated_at DESC
                LIMIT 1
                """
            ).fetchone()
        return None if row is None else DataCoverageResponse.model_validate(_load(row[0]))

    def save_enrichment(
        self,
        *,
        run_id: str,
        mode: str,
        analysis_mode: str,
        provider: str,
        request_budget: int,
        request_count: int,
        model_call_budget: int,
        model_call_count: int,
        data_cutoff: datetime,
        status: str,
        started_at: datetime,
        completed_at: datetime,
        items: list[CandidateEnrichmentItem],
    ) -> None:
        with get_connection() as connection:
            connection.execute("BEGIN TRANSACTION")
            try:
                connection.execute(
                    """
                    INSERT INTO candidate_enrichment_runs (
                        run_id, mode, analysis_mode, provider,
                        request_budget, request_count, model_call_budget,
                        model_call_count, data_cutoff, candidate_count,
                        status, started_at, completed_at, error_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT (run_id) DO NOTHING
                    """,
                    [
                        run_id,
                        mode,
                        analysis_mode,
                        provider,
                        request_budget,
                        request_count,
                        model_call_budget,
                        model_call_count,
                        data_cutoff,
                        len(items),
                        status,
                        started_at,
                        completed_at,
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO candidate_enrichment_items (
                        run_id, symbol, enrichment_status,
                        fetched_data_types_json, missing_data_types_json,
                        elapsed_time, risk_flags_json, request_count,
                        model_call_count, error_message
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (run_id, symbol) DO NOTHING
                    """,
                    [
                        [
                            run_id,
                            item.symbol,
                            item.enrichment_status,
                            _dump(item.fetched_data_types),
                            _dump(item.missing_data_types),
                            item.elapsed_time,
                            _dump(item.risk_flags),
                            item.request_count,
                            item.model_call_count,
                            item.error_message,
                        ]
                        for item in items
                    ],
                )
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise


__all__ = ["FullMarketRepository"]
