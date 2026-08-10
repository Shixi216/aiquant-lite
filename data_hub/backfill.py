from __future__ import annotations

import difflib
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from config.settings import settings
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from data_hub.schemas.unified import VerificationStatus
from data_hub.services.canonicalization_service import (
    CanonicalizationService,
)
from data_hub.services.event_cluster_service import (
    EVENT_WINDOW,
    EventClusterService,
    normalize_title,
)
from database.db import get_connection, initialize_database


SUPPORTED_CANONICAL_MARKET = {
    DataType.DAILY_BAR,
    DataType.REALTIME_QUOTE,
}
SUPPORTED_EVENTS = {
    DataType.ANNOUNCEMENT,
    DataType.FINANCE_NEWS,
}
COMPLETED_ITEM_STATUSES = {
    "APPLIED",
    "VERIFIED",
    "SINGLE_SOURCE",
    "CONFLICT",
    "SKIPPED",
}
REPORT_VERSION = "historical-backfill-report-v1"


@dataclass(frozen=True)
class BackfillOptions:
    apply: bool = False
    data_types: tuple[str, ...] = ()
    symbol: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int | None = None
    resume: str | None = None
    report_path: Path | None = None
    database_path: Path | None = None
    max_work_items: int | None = None

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 1:
            raise ValueError("limit must be at least 1")
        if self.start_time and (
            self.start_time.tzinfo is None
            or self.start_time.utcoffset() is None
        ):
            raise ValueError("start_time must include a timezone")
        if self.end_time and (
            self.end_time.tzinfo is None
            or self.end_time.utcoffset() is None
        ):
            raise ValueError("end_time must include a timezone")
        if (
            self.start_time
            and self.end_time
            and self.start_time > self.end_time
        ):
            raise ValueError("start_time must not be later than end_time")
        if self.resume and not self.apply:
            raise ValueError("--resume requires --apply")
        invalid = sorted(
            set(self.data_types)
            - {data_type.value for data_type in DataType}
        )
        if invalid:
            raise ValueError(
                "unsupported data types: " + ", ".join(invalid)
            )

    @property
    def path(self) -> Path:
        return (
            self.database_path or settings.opc_database_path
        ).expanduser().resolve()

    def filters(self) -> dict[str, Any]:
        return {
            "data_types": list(self.data_types),
            "symbol": self.symbol,
            "start_time": (
                self.start_time.isoformat()
                if self.start_time
                else None
            ),
            "end_time": (
                self.end_time.isoformat()
                if self.end_time
                else None
            ),
            "limit": self.limit,
        }


@dataclass(frozen=True)
class WorkItem:
    work_key: str
    kind: str
    data_type: str
    symbol: str | None
    records: tuple[MarketRecord, ...] = ()
    source_record_ids: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def raw_count(self) -> int:
        return max(len(self.source_record_ids), len(self.records), 1)


@dataclass(frozen=True)
class ItemResult:
    item: WorkItem
    status: str
    result_type: str | None = None
    result_id: str | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _stable_key(value: Any) -> str:
    return hashlib.sha256(_json_dump(value).encode("utf-8")).hexdigest()


def _row_to_record(row: tuple[Any, ...]) -> MarketRecord:
    payload = json.loads(row[10]) if isinstance(row[10], str) else row[10]
    return MarketRecord(
        record_id=row[0],
        symbol=row[1],
        data_type=row[2],
        event_time=row[3],
        fetched_at=row[4],
        source_name=row[5],
        source_url=row[6],
        source_level=row[7],
        verified=row[8],
        content_hash=row[9],
        data=payload,
    )


def _validation_error(record: MarketRecord) -> str | None:
    if record.data_type == DataType.DAILY_BAR:
        missing = [
            field
            for field in ("trade_date", "open", "high", "low", "close")
            if record.data.get(field) is None
        ]
        if missing:
            return "missing daily-bar fields: " + ", ".join(missing)
    elif record.data_type == DataType.REALTIME_QUOTE:
        missing = [
            field
            for field in ("open", "high", "low", "close")
            if record.data.get(field) is None
        ]
        if missing:
            return "missing realtime-quote fields: " + ", ".join(missing)
    elif record.data_type == DataType.FINANCIAL_STATEMENT:
        if not str(record.data.get("statement_type") or "").strip():
            return "missing financial statement_type"
    elif record.data_type in SUPPORTED_EVENTS:
        if not str(record.data.get("title") or "").strip():
            return "missing event title"
    return None


def _load_records(
    options: BackfillOptions,
) -> tuple[int, list[MarketRecord], list[WorkItem]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if options.data_types:
        placeholders = ", ".join("?" for _ in options.data_types)
        clauses.append(f"data_type IN ({placeholders})")
        parameters.extend(options.data_types)
    if options.symbol:
        clauses.append("symbol = ?")
        parameters.append(options.symbol)
    if options.start_time:
        clauses.append("event_time >= ?")
        parameters.append(options.start_time)
    if options.end_time:
        clauses.append("event_time <= ?")
        parameters.append(options.end_time)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = "LIMIT ?" if options.limit is not None else ""
    if options.limit is not None:
        parameters.append(options.limit)

    connection = duckdb.connect(str(options.path), read_only=True)
    try:
        original_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM data_records"
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT record_id, symbol, data_type, event_time, fetched_at,
                   source_name, source_url, source_level, verified,
                   content_hash, payload_json
            FROM data_records
            {where}
            ORDER BY data_type, symbol, event_time, source_name, record_id
            {limit}
            """,
            parameters,
        ).fetchall()
    finally:
        connection.close()

    valid: list[MarketRecord] = []
    prebuilt: list[WorkItem] = []
    for row in rows:
        try:
            record = _row_to_record(row)
            error = _validation_error(record)
            if error:
                prebuilt.append(
                    WorkItem(
                        work_key="invalid:" + row[0],
                        kind="invalid",
                        data_type=row[2],
                        symbol=row[1],
                        source_record_ids=(row[0],),
                        reason=error,
                    )
                )
            else:
                valid.append(record)
        except Exception as exc:
            prebuilt.append(
                WorkItem(
                    work_key="invalid:" + str(row[0]),
                    kind="invalid",
                    data_type=str(row[2]),
                    symbol=str(row[1]),
                    source_record_ids=(str(row[0]),),
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )
    return original_count, valid, prebuilt


def _build_work_items(
    valid: list[MarketRecord],
    prebuilt: list[WorkItem],
) -> list[WorkItem]:
    groups: dict[tuple[Any, ...], list[MarketRecord]] = defaultdict(list)
    items = list(prebuilt)
    for record in valid:
        if record.data_type in SUPPORTED_CANONICAL_MARKET:
            key = (
                "market",
                record.data_type.value,
                record.symbol,
                record.event_time.astimezone(timezone.utc).isoformat(),
            )
            groups[key].append(record)
        elif record.data_type == DataType.FINANCIAL_STATEMENT:
            statement_type = str(record.data["statement_type"]).casefold()
            key = (
                "financial",
                record.symbol,
                statement_type,
                record.event_time.astimezone(timezone.utc).isoformat(),
            )
            groups[key].append(record)
        elif record.data_type in SUPPORTED_EVENTS:
            items.append(
                WorkItem(
                    work_key=f"event:{record.record_id}",
                    kind="event",
                    data_type=record.data_type.value,
                    symbol=record.symbol,
                    records=(record,),
                    source_record_ids=(record.record_id,),
                )
            )
        elif record.data_type == DataType.STOCK_BASIC:
            items.append(
                WorkItem(
                    work_key=f"skip:{record.record_id}",
                    kind="skip",
                    data_type=record.data_type.value,
                    symbol=record.symbol,
                    source_record_ids=(record.record_id,),
                    reason=(
                        "stock_basic is identity/reference data and does not "
                        "fit the OHLC canonical market payload"
                    ),
                )
            )
        else:
            items.append(
                WorkItem(
                    work_key=f"skip:{record.record_id}",
                    kind="skip",
                    data_type=record.data_type.value,
                    symbol=record.symbol,
                    source_record_ids=(record.record_id,),
                    reason="data type is not supported by this backfill",
                )
            )
    for key, records in groups.items():
        kind = str(key[0])
        ordered = tuple(
            sorted(records, key=lambda item: (item.source_name, item.record_id))
        )
        items.append(
            WorkItem(
                work_key=f"{kind}:{_stable_key(key)}",
                kind=kind,
                data_type=ordered[0].data_type.value,
                symbol=ordered[0].symbol,
                records=ordered,
                source_record_ids=tuple(
                    record.record_id for record in ordered
                ),
            )
        )
    return sorted(items, key=lambda item: item.work_key)


def _preview_event_groups(
    records: list[MarketRecord],
) -> list[list[MarketRecord]]:
    groups: list[list[MarketRecord]] = []
    for record in sorted(records, key=lambda item: (item.event_time, item.record_id)):
        matched: list[MarketRecord] | None = None
        for group in groups:
            representative = group[0]
            if representative.data_type != record.data_type:
                continue
            if abs(record.event_time - representative.event_time) > EVENT_WINDOW:
                continue
            same_hash = bool(
                record.content_hash
                and any(
                    item.content_hash == record.content_hash
                    for item in group
                )
            )
            same_title = (
                normalize_title(str(representative.data["title"]))
                == normalize_title(str(record.data["title"]))
            )
            same_symbol = any(
                item.symbol == record.symbol for item in group
            )
            if same_hash or (same_title and same_symbol):
                matched = group
                break
        if matched is None:
            groups.append([record])
        else:
            matched.append(record)
    return groups


def _suspected_duplicates(
    clusters: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    suspects: list[dict[str, Any]] = []
    for index, first in enumerate(clusters):
        for second in clusters[index + 1 :]:
            if first["event_type"] != second["event_type"]:
                continue
            if not set(first["symbols"]) & set(second["symbols"]):
                continue
            first_time = datetime.fromisoformat(first["event_time"])
            second_time = datetime.fromisoformat(second["event_time"])
            if abs(first_time - second_time) > timedelta(hours=48):
                continue
            similarity = difflib.SequenceMatcher(
                None,
                normalize_title(first["title"]),
                normalize_title(second["title"]),
            ).ratio()
            if similarity >= 0.82:
                suspects.append(
                    {
                        "first_event_cluster_id": first["id"],
                        "second_event_cluster_id": second["id"],
                        "title_similarity": round(similarity, 4),
                        "first_title": first["title"],
                        "second_title": second["title"],
                    }
                )
    return suspects


def _preview_events(records: list[MarketRecord]) -> dict[str, Any]:
    clusters: list[dict[str, Any]] = []
    for group in _preview_event_groups(records):
        primary = min(
            group,
            key=lambda item: (
                0 if item.source_level == SourceLevel.OFFICIAL else 1,
                item.event_time,
                item.record_id,
            ),
        )
        cluster_id = "preview:" + _stable_key(
            {
                "type": primary.data_type.value,
                "title": normalize_title(str(primary.data["title"])),
                "symbol": primary.symbol,
                "date": primary.event_time.date().isoformat(),
            }
        )[:24]
        clusters.append(
            {
                "id": cluster_id,
                "title": str(primary.data["title"]),
                "event_type": primary.data_type.value,
                "event_time": primary.event_time.isoformat(),
                "primary_source_id": primary.record_id,
                "source_record_ids": sorted(
                    item.record_id for item in group
                ),
                "symbols": sorted({item.symbol for item in group}),
            }
        )
    return {
        "clusters": clusters,
        "suspected_duplicates": _suspected_duplicates(clusters),
    }


def _process_item(
    item: WorkItem,
    *,
    apply: bool,
    canonical_service: CanonicalizationService,
    event_service: EventClusterService | None,
) -> ItemResult:
    if item.kind == "invalid":
        return ItemResult(
            item=item,
            status="FAILED",
            reason=item.reason,
        )
    if item.kind == "skip":
        return ItemResult(
            item=item,
            status="SKIPPED",
            reason=item.reason,
        )
    try:
        if item.kind == "market":
            output = canonical_service.canonicalize_market(
                list(item.records),
                persist=apply,
            )
            return ItemResult(
                item=item,
                status=output.verification_status.value,
                result_type="canonical_market_record",
                result_id=output.canonical_record_id,
                details={
                    "confidence": output.confidence,
                    "primary_source": output.primary_source,
                    "field_differences": output.field_differences,
                },
            )
        if item.kind == "financial":
            output = canonical_service.canonicalize_financial(
                list(item.records),
                persist=apply,
            )
            return ItemResult(
                item=item,
                status=output.verification_status.value,
                result_type="canonical_financial_record",
                result_id=output.canonical_record_id,
                details={
                    "confidence": output.confidence,
                    "primary_source": output.primary_source,
                    "field_differences": output.field_differences,
                },
            )
        if item.kind == "event":
            if not apply or event_service is None:
                return ItemResult(
                    item=item,
                    status="APPLIED",
                    result_type="event_source",
                    result_id=item.source_record_ids[0],
                )
            clusters = event_service.cluster(list(item.records))
            cluster = clusters[0]
            return ItemResult(
                item=item,
                status="APPLIED",
                result_type="event_cluster",
                result_id=cluster.event_cluster_id,
                details={
                    "primary_source_id": cluster.primary_source_id,
                    "source_count": cluster.source_count,
                },
            )
        raise ValueError(f"unknown work item kind: {item.kind}")
    except Exception as exc:
        return ItemResult(
            item=item,
            status="FAILED",
            reason=f"{type(exc).__name__}: {exc}",
        )


def _insert_run(
    run_id: str,
    options: BackfillOptions,
    original_count: int,
    selected_count: int,
    started_at: datetime,
) -> None:
    with get_connection() as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                INSERT INTO backfill_runs (
                    backfill_run_id, mode, status, filters_json,
                    original_record_count, selected_record_count,
                    processed_count, success_count, skipped_count,
                    conflict_count, failed_count, started_at
                )
                VALUES (?, 'APPLY', 'RUNNING', ?, ?, ?, 0, 0, 0, 0, 0, ?)
                """,
                [
                    run_id,
                    _json_dump(options.filters()),
                    original_count,
                    selected_count,
                    started_at,
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def _resume_run(
    requested: str,
) -> tuple[str, dict[str, Any]]:
    initialize_database()
    with get_connection() as connection:
        if requested == "latest":
            row = connection.execute(
                """
                SELECT backfill_run_id, filters_json
                FROM backfill_runs
                WHERE status IN (
                    'RUNNING', 'INTERRUPTED', 'PARTIAL', 'FAILED'
                )
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT backfill_run_id, filters_json
                FROM backfill_runs
                WHERE backfill_run_id = ?
                """,
                [requested],
            ).fetchone()
    if row is None:
        raise ValueError(f"backfill run is not resumable: {requested}")
    filters = json.loads(row[1]) if isinstance(row[1], str) else row[1]
    return row[0], filters


def _completed_keys(run_id: str) -> set[str]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT work_key
            FROM backfill_items
            WHERE backfill_run_id = ?
              AND status IN (
                  'APPLIED', 'VERIFIED', 'SINGLE_SOURCE',
                  'CONFLICT', 'SKIPPED'
              )
            """,
            [run_id],
        ).fetchall()
    return {row[0] for row in rows}


def _write_item(run_id: str, result: ItemResult) -> None:
    with get_connection() as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                INSERT INTO backfill_items (
                    backfill_run_id, work_key, data_type, symbol,
                    source_record_ids_json, raw_record_count, status,
                    result_type, result_id, reason, details_json,
                    processed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (backfill_run_id, work_key) DO UPDATE SET
                    status = EXCLUDED.status,
                    result_type = EXCLUDED.result_type,
                    result_id = EXCLUDED.result_id,
                    reason = EXCLUDED.reason,
                    details_json = EXCLUDED.details_json,
                    processed_at = EXCLUDED.processed_at
                """,
                [
                    run_id,
                    result.item.work_key,
                    result.item.data_type,
                    result.item.symbol,
                    _json_dump(result.item.source_record_ids),
                    result.item.raw_count,
                    result.status,
                    result.result_type,
                    result.result_id,
                    result.reason,
                    _json_dump(result.details),
                    datetime.now().astimezone(),
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def _aggregate_results(results: list[ItemResult]) -> dict[str, Any]:
    processed_count = sum(result.item.raw_count for result in results)
    success_count = sum(
        result.item.raw_count
        for result in results
        if result.status
        in {"APPLIED", "VERIFIED", "SINGLE_SOURCE", "CONFLICT"}
    )
    skipped_count = sum(
        result.item.raw_count
        for result in results
        if result.status == "SKIPPED"
    )
    failed_count = sum(
        result.item.raw_count
        for result in results
        if result.status == "FAILED"
    )
    status_counts = {
        status: sum(1 for result in results if result.status == status)
        for status in (
            "APPLIED",
            "VERIFIED",
            "SINGLE_SOURCE",
            "CONFLICT",
            "SKIPPED",
            "FAILED",
        )
    }
    by_type: dict[str, dict[str, int]] = {}
    for result in results:
        stats = by_type.setdefault(
            result.item.data_type,
            {
                "processed_records": 0,
                "successful_records": 0,
                "skipped_records": 0,
                "failed_records": 0,
                "work_items": 0,
            },
        )
        stats["processed_records"] += result.item.raw_count
        stats["work_items"] += 1
        if result.status == "SKIPPED":
            stats["skipped_records"] += result.item.raw_count
        elif result.status == "FAILED":
            stats["failed_records"] += result.item.raw_count
        else:
            stats["successful_records"] += result.item.raw_count
    return {
        "processed_count": processed_count,
        "success_count": success_count,
        "skipped_count": skipped_count,
        "conflict_count": status_counts["CONFLICT"],
        "failed_count": failed_count,
        "status_counts": status_counts,
        "data_type_results": by_type,
    }


def _read_run_results(run_id: str) -> list[ItemResult]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT work_key, data_type, symbol, source_record_ids_json,
                   raw_record_count, status, result_type, result_id,
                   reason, details_json
            FROM backfill_items
            WHERE backfill_run_id = ?
            ORDER BY work_key
            """,
            [run_id],
        ).fetchall()
    results: list[ItemResult] = []
    for row in rows:
        source_ids = (
            json.loads(row[3]) if isinstance(row[3], str) else row[3]
        )
        # raw_count is represented by source IDs for normal items. Preserve
        # the stored count for malformed records that have no model instance.
        if len(source_ids) < int(row[4]):
            source_ids.extend(
                f"unknown:{index}"
                for index in range(int(row[4]) - len(source_ids))
            )
        item = WorkItem(
            work_key=row[0],
            kind="audit",
            data_type=row[1],
            symbol=row[2],
            source_record_ids=tuple(source_ids),
        )
        details = (
            json.loads(row[9]) if isinstance(row[9], str) else row[9]
        )
        results.append(
            ItemResult(
                item=item,
                status=row[5],
                result_type=row[6],
                result_id=row[7],
                reason=row[8],
                details=details,
            )
        )
    return results


def _database_totals(path: Path) -> dict[str, int]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        return {
            "canonical_market_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM canonical_market_records"
                ).fetchone()[0]
            ),
            "canonical_financial_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM canonical_financial_records"
                ).fetchone()[0]
            ),
            "event_cluster_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM event_clusters"
                ).fetchone()[0]
            ),
            "event_source_link_count": int(
                connection.execute(
                    "SELECT COUNT(*) FROM event_source_links"
                ).fetchone()[0]
            ),
        }
    finally:
        connection.close()


def _persisted_event_summary(path: Path) -> dict[str, Any]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT c.event_cluster_id, c.canonical_title, c.event_type,
                   c.event_time,
                   list(s.symbol ORDER BY s.symbol) AS symbols
            FROM event_clusters c
            JOIN event_symbol_links s
              ON s.event_cluster_id = c.event_cluster_id
            GROUP BY c.event_cluster_id, c.canonical_title,
                     c.event_type, c.event_time
            ORDER BY c.event_time, c.event_cluster_id
            """
        ).fetchall()
    finally:
        connection.close()
    clusters = [
        {
            "id": row[0],
            "title": row[1],
            "event_type": row[2],
            "event_time": row[3].isoformat(),
            "symbols": list(row[4]),
        }
        for row in rows
    ]
    return {
        "clusters": clusters,
        "suspected_duplicates": _suspected_duplicates(clusters),
    }


def _report_paths(
    run_id: str,
    requested: Path | None,
) -> tuple[Path, Path]:
    if requested is None:
        base = Path("reports") / "backfill" / run_id
    else:
        base = requested
        if base.suffix.lower() in {".json", ".md"}:
            base = base.with_suffix("")
        elif base.exists() and base.is_dir():
            base = base / run_id
    return base.with_suffix(".json"), base.with_suffix(".md")


def _markdown(report: dict[str, Any]) -> str:
    totals = report["database_totals"]
    statuses = report["status_counts"]
    lines = [
        f"# Hermes-OPC 历史数据回填审计：{report['backfill_run_id']}",
        "",
        f"- 模式：{report['mode']}",
        f"- 状态：{report['status']}",
        f"- 原始记录总数：{report['original_record_count']}",
        f"- 选中记录数：{report['selected_record_count']}",
        f"- 实际处理数：{report['processed_count']}",
        f"- 成功数：{report['success_count']}",
        f"- 跳过数：{report['skipped_count']}",
        f"- 失败数：{report['failed_count']}",
        "",
        "## 统一数据层数量",
        "",
        f"- Canonical market：{totals['canonical_market_count']}",
        f"- Canonical financial：{totals['canonical_financial_count']}",
        f"- Event clusters：{totals['event_cluster_count']}",
        f"- Event source links：{totals['event_source_link_count']}",
        "",
        "## 数据质量",
        "",
        f"- VERIFIED：{statuses['VERIFIED']}",
        f"- SINGLE_SOURCE：{statuses['SINGLE_SOURCE']}",
        f"- CONFLICT：{statuses['CONFLICT']}",
        f"- SKIPPED：{statuses['SKIPPED']}",
        f"- FAILED：{statuses['FAILED']}",
        "",
        "## 疑似重复事件",
        "",
    ]
    suspects = report["suspected_duplicate_events"]
    if not suspects:
        lines.append("- 无")
    else:
        for suspect in suspects:
            lines.append(
                "- "
                f"{suspect['first_event_cluster_id']} ↔ "
                f"{suspect['second_event_cluster_id']} "
                f"(similarity={suspect['title_similarity']})"
            )
    lines.extend(["", "## 跳过和失败", ""])
    if not report["skipped_records"] and not report["failed_records"]:
        lines.append("- 无")
    for item in report["skipped_records"]:
        lines.append(
            f"- SKIPPED {item['source_record_ids']}: {item['reason']}"
        )
    for item in report["failed_records"]:
        lines.append(
            f"- FAILED {item['source_record_ids']}: {item['reason']}"
        )
    lines.extend(
        [
            "",
            "> 本报告仅保存结构化结果和审计信息，不包含隐藏思维链。",
            "",
        ]
    )
    return "\n".join(lines)


def _write_report(
    report: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    markdown_path.write_text(_markdown(report), encoding="utf-8")


def _update_run(
    run_id: str,
    *,
    status: str,
    aggregate: dict[str, Any],
    json_path: Path,
    markdown_path: Path,
    completed_at: datetime | None,
) -> None:
    with get_connection() as connection:
        connection.execute("BEGIN TRANSACTION")
        try:
            connection.execute(
                """
                UPDATE backfill_runs
                SET status = ?, processed_count = ?, success_count = ?,
                    skipped_count = ?, conflict_count = ?,
                    failed_count = ?, report_json_path = ?,
                    report_markdown_path = ?, completed_at = ?
                WHERE backfill_run_id = ?
                """,
                [
                    status,
                    aggregate["processed_count"],
                    aggregate["success_count"],
                    aggregate["skipped_count"],
                    aggregate["conflict_count"],
                    aggregate["failed_count"],
                    str(json_path.resolve()),
                    str(markdown_path.resolve()),
                    completed_at,
                    run_id,
                ],
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise


def _options_from_resume(
    options: BackfillOptions,
    filters: dict[str, Any],
) -> BackfillOptions:
    requested = options.filters()
    has_explicit_filters = any(
        value not in (None, [], ())
        for value in requested.values()
    )
    if has_explicit_filters and requested != filters:
        raise ValueError("resume filters do not match the original run")
    return BackfillOptions(
        apply=True,
        data_types=tuple(filters.get("data_types") or ()),
        symbol=filters.get("symbol"),
        start_time=(
            datetime.fromisoformat(filters["start_time"])
            if filters.get("start_time")
            else None
        ),
        end_time=(
            datetime.fromisoformat(filters["end_time"])
            if filters.get("end_time")
            else None
        ),
        limit=filters.get("limit"),
        resume=options.resume,
        report_path=options.report_path,
        database_path=options.database_path,
        max_work_items=options.max_work_items,
    )


def run_backfill(options: BackfillOptions) -> dict[str, Any]:
    if not options.path.exists():
        raise FileNotFoundError(f"DuckDB database does not exist: {options.path}")

    started_at = datetime.now().astimezone()
    run_id: str
    if options.resume:
        run_id, filters = _resume_run(options.resume)
        options = _options_from_resume(options, filters)
    else:
        run_id = (
            "backfill_"
            + started_at.strftime("%Y%m%dT%H%M%S")
            + "_"
            + uuid4().hex[:8]
        )

    original_count, valid, prebuilt = _load_records(options)
    work_items = _build_work_items(valid, prebuilt)
    selected_count = sum(item.raw_count for item in work_items)
    event_records = [
        item.records[0]
        for item in work_items
        if item.kind == "event"
    ]
    event_preview = _preview_events(event_records)

    if options.apply:
        initialize_database()
        if not options.resume:
            _insert_run(
                run_id,
                options,
                original_count,
                selected_count,
                started_at,
            )
        else:
            with get_connection() as connection:
                connection.execute(
                    """
                    UPDATE backfill_runs
                    SET status = 'RUNNING', completed_at = NULL
                    WHERE backfill_run_id = ?
                    """,
                    [run_id],
                )
        completed = _completed_keys(run_id)
        canonical_service = CanonicalizationService()
        event_service: EventClusterService | None = EventClusterService()
    else:
        completed = set()
        canonical_service = CanonicalizationService(
            initialize_repositories=False
        )
        event_service = None

    current_results: list[ItemResult] = []
    processed_this_invocation = 0
    interrupted = False
    for item in work_items:
        if item.work_key in completed:
            continue
        if (
            options.max_work_items is not None
            and processed_this_invocation >= options.max_work_items
        ):
            interrupted = True
            break
        result = _process_item(
            item,
            apply=options.apply,
            canonical_service=canonical_service,
            event_service=event_service,
        )
        current_results.append(result)
        processed_this_invocation += 1
        if options.apply:
            _write_item(run_id, result)

    results = (
        _read_run_results(run_id)
        if options.apply
        else current_results
    )
    aggregate = _aggregate_results(results)
    if interrupted:
        status = "INTERRUPTED"
    elif aggregate["failed_count"]:
        status = "PARTIAL"
    else:
        status = "COMPLETED"

    if options.apply:
        database_totals = _database_totals(options.path)
        event_summary = _persisted_event_summary(options.path)
    else:
        market_outputs = sum(
            1 for result in results
            if result.result_type == "canonical_market_record"
        )
        financial_outputs = sum(
            1 for result in results
            if result.result_type == "canonical_financial_record"
        )
        database_totals = {
            "canonical_market_count": market_outputs,
            "canonical_financial_count": financial_outputs,
            "event_cluster_count": len(event_preview["clusters"]),
            "event_source_link_count": len(event_records),
        }
        event_summary = event_preview

    skipped = [
        {
            "source_record_ids": list(result.item.source_record_ids),
            "reason": result.reason,
        }
        for result in results
        if result.status == "SKIPPED"
    ]
    failed = [
        {
            "source_record_ids": list(result.item.source_record_ids),
            "reason": result.reason,
        }
        for result in results
        if result.status == "FAILED"
    ]
    conflicts = [
        {
            "result_id": result.result_id,
            "symbol": result.item.symbol,
            "source_record_ids": list(result.item.source_record_ids),
            "confidence": result.details.get("confidence"),
            "field_differences": result.details.get(
                "field_differences",
                {},
            ),
        }
        for result in results
        if result.status == VerificationStatus.CONFLICT.value
    ]
    json_path, markdown_path = _report_paths(
        run_id,
        options.report_path,
    )
    report = {
        "report_version": REPORT_VERSION,
        "backfill_run_id": run_id,
        "mode": "APPLY" if options.apply else "DRY_RUN",
        "status": status,
        "database_path": str(options.path),
        "filters": options.filters(),
        "started_at": started_at.isoformat(),
        "completed_at": (
            None if interrupted else datetime.now().astimezone().isoformat()
        ),
        "original_record_count": original_count,
        "selected_record_count": selected_count,
        **aggregate,
        "database_totals": database_totals,
        "conflict_records": conflicts,
        "skipped_records": skipped,
        "failed_records": failed,
        "missing_data": [
            *[
                {
                    "source_record_ids": item["source_record_ids"],
                    "reason": item["reason"],
                }
                for item in skipped
            ],
            *[
                {
                    "source_record_ids": item["source_record_ids"],
                    "reason": item["reason"],
                }
                for item in failed
            ],
        ],
        "suspected_duplicate_events": event_summary[
            "suspected_duplicates"
        ],
        "report_json_path": str(json_path.resolve()),
        "report_markdown_path": str(markdown_path.resolve()),
    }
    _write_report(report, json_path, markdown_path)

    if options.apply:
        _update_run(
            run_id,
            status=status,
            aggregate=aggregate,
            json_path=json_path,
            markdown_path=markdown_path,
            completed_at=(
                None
                if interrupted
                else datetime.fromisoformat(report["completed_at"])
            ),
        )
    return report


__all__ = [
    "BackfillOptions",
    "REPORT_VERSION",
    "run_backfill",
]
