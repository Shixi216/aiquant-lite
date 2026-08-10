from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config.settings import settings
from data_hub.backfill import BackfillOptions, run_backfill
from data_hub.schemas.market import DataType, MarketRecord, SourceLevel
from database.db import get_connection, initialize_database, insert_market_record
from scripts.backfill_unified_data import build_parser


SHANGHAI = ZoneInfo("Asia/Shanghai")
BASE_TIME = datetime(2026, 7, 20, 15, tzinfo=SHANGHAI)


@pytest.fixture()
def backfill_database(tmp_path, monkeypatch) -> Path:
    database_path = tmp_path / "backfill.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", database_path)
    initialize_database()
    return database_path


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _daily(
    record_id: str,
    source_name: str,
    *,
    close: float,
    event_time: datetime = BASE_TIME,
    malformed: bool = False,
) -> MarketRecord:
    payload = {
        "trade_date": event_time.strftime("%Y%m%d"),
        "open": 9.9,
        "high": max(10.2, close),
        "low": 9.8,
        "close": close,
        "amount": 10_000_000,
    }
    if malformed:
        payload.pop("close")
    if source_name == "Tushare Pro":
        payload["vol"] = 10_000
        payload["amount"] = 10_000
    elif source_name == "AKShare / Eastmoney":
        payload["volume"] = 10_000
    else:
        payload["volume"] = 1_000_000
    return MarketRecord(
        record_id=record_id,
        symbol="600000.SH",
        data_type=DataType.DAILY_BAR,
        event_time=event_time,
        fetched_at=event_time + timedelta(minutes=5),
        source_name=source_name,
        source_level=SourceLevel.STRUCTURED,
        verified=False,
        content_hash=_hash(record_id),
        data=payload,
    )


def _financial(record_id: str) -> MarketRecord:
    return MarketRecord(
        record_id=record_id,
        symbol="600000.SH",
        data_type=DataType.FINANCIAL_STATEMENT,
        event_time=BASE_TIME,
        fetched_at=BASE_TIME + timedelta(minutes=10),
        source_name="Tushare Pro / 利润表",
        source_level=SourceLevel.STRUCTURED,
        verified=False,
        content_hash=_hash(record_id),
        data={
            "statement_type": "income",
            "report_period": "20260630",
            "revenue": 1_000_000,
        },
    )


def _event(
    record_id: str,
    *,
    source_level: SourceLevel,
    source_name: str,
    minutes: int,
) -> MarketRecord:
    event_time = BASE_TIME + timedelta(minutes=minutes)
    return MarketRecord(
        record_id=record_id,
        symbol="600000.SH",
        data_type=DataType.ANNOUNCEMENT,
        event_time=event_time,
        fetched_at=event_time + timedelta(minutes=2),
        source_name=source_name,
        source_level=source_level,
        verified=source_level == SourceLevel.OFFICIAL,
        content_hash=_hash(f"{source_name}:{record_id}"),
        data={
            "title": "公司发布重大资产重组公告"
            + ("！" if source_level == SourceLevel.MEDIA else ""),
            "symbol": "600000.SH",
        },
    )


def _stock_basic(record_id: str) -> MarketRecord:
    return MarketRecord(
        record_id=record_id,
        symbol="600000.SH",
        data_type=DataType.STOCK_BASIC,
        event_time=BASE_TIME,
        fetched_at=BASE_TIME,
        source_name="Tushare Pro",
        source_level=SourceLevel.STRUCTURED,
        verified=True,
        content_hash=_hash(record_id),
        data={"symbol": "600000.SH", "name": "Test"},
    )


def _seed(
    *,
    include_conflict: bool = False,
    include_bad: bool = False,
    include_skip: bool = True,
) -> list[MarketRecord]:
    records = [
        _daily("verified-ts", "Tushare Pro", close=10.0),
        _daily("verified-bs", "BaoStock", close=10.0),
        _financial("financial-one"),
        _event(
            "event-media",
            source_level=SourceLevel.MEDIA,
            source_name="Media",
            minutes=0,
        ),
        _event(
            "event-official",
            source_level=SourceLevel.OFFICIAL,
            source_name="CNInfo",
            minutes=5,
        ),
    ]
    if include_conflict:
        conflict_time = BASE_TIME + timedelta(days=1)
        records.extend(
            [
                _daily(
                    "conflict-ts",
                    "Tushare Pro",
                    close=10.0,
                    event_time=conflict_time,
                ),
                _daily(
                    "conflict-bs",
                    "BaoStock",
                    close=10.5,
                    event_time=conflict_time,
                ),
            ]
        )
    if include_bad:
        records.append(
            _daily(
                "bad-daily",
                "BaoStock",
                close=10.0,
                event_time=BASE_TIME + timedelta(days=2),
                malformed=True,
            )
        )
    if include_skip:
        records.append(_stock_basic("stock-basic"))
    with get_connection() as connection:
        for record in records:
            insert_market_record(connection, record)
    return records


def _counts() -> dict[str, int]:
    with get_connection() as connection:
        return {
            table: int(
                connection.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
            )
            for table in (
                "data_records",
                "canonical_market_records",
                "canonical_financial_records",
                "event_clusters",
                "event_source_links",
                "backfill_runs",
                "backfill_items",
            )
        }


def _raw_snapshot() -> list[tuple[object, ...]]:
    with get_connection() as connection:
        return connection.execute(
            """
            SELECT record_id, verified, content_hash, payload_json
            FROM data_records
            ORDER BY record_id
            """
        ).fetchall()


def test_cli_defaults_to_dry_run() -> None:
    args = build_parser().parse_args([])

    assert args.apply is False
    assert args.dry_run is False


def test_dry_run_does_not_write_database(
    backfill_database,
    tmp_path,
) -> None:
    _seed()
    before = _counts()
    raw_before = _raw_snapshot()

    report = run_backfill(
        BackfillOptions(
            report_path=tmp_path / "dry-run-report",
        )
    )

    assert report["mode"] == "DRY_RUN"
    assert report["status"] == "COMPLETED"
    assert report["database_totals"]["canonical_market_count"] == 1
    assert report["database_totals"]["canonical_financial_count"] == 1
    assert report["database_totals"]["event_cluster_count"] == 1
    assert _counts() == before
    assert _raw_snapshot() == raw_before
    assert Path(report["report_json_path"]).exists()
    assert Path(report["report_markdown_path"]).exists()


def test_apply_writes_expected_layers_and_preserves_raw(
    backfill_database,
    tmp_path,
) -> None:
    records = _seed()
    raw_before = _raw_snapshot()

    report = run_backfill(
        BackfillOptions(
            apply=True,
            report_path=tmp_path / "apply-report",
        )
    )

    assert report["status"] == "COMPLETED"
    assert report["original_record_count"] == len(records)
    assert report["processed_count"] == len(records)
    assert report["database_totals"] == {
        "canonical_market_count": 1,
        "canonical_financial_count": 1,
        "event_cluster_count": 1,
        "event_source_link_count": 2,
    }
    assert report["skipped_count"] == 1
    assert _raw_snapshot() == raw_before
    with get_connection() as connection:
        primary = connection.execute(
            "SELECT primary_source_id FROM event_clusters"
        ).fetchone()[0]
    assert primary == "event-official"


def test_second_apply_is_idempotent(
    backfill_database,
    tmp_path,
) -> None:
    _seed()
    first = run_backfill(
        BackfillOptions(
            apply=True,
            report_path=tmp_path / "first",
        )
    )
    counts_after_first = _counts()

    second = run_backfill(
        BackfillOptions(
            apply=True,
            report_path=tmp_path / "second",
        )
    )

    assert first["database_totals"] == second["database_totals"]
    after_second = _counts()
    for table in (
        "data_records",
        "canonical_market_records",
        "canonical_financial_records",
        "event_clusters",
        "event_source_links",
    ):
        assert after_second[table] == counts_after_first[table]
    assert after_second["backfill_runs"] == 2


def test_interrupted_run_can_resume(
    backfill_database,
    tmp_path,
) -> None:
    records = _seed(include_conflict=True)
    interrupted = run_backfill(
        BackfillOptions(
            apply=True,
            max_work_items=2,
            report_path=tmp_path / "interrupted",
        )
    )

    assert interrupted["status"] == "INTERRUPTED"
    resumed = run_backfill(
        BackfillOptions(
            apply=True,
            resume=interrupted["backfill_run_id"],
            report_path=tmp_path / "resumed",
        )
    )

    assert resumed["backfill_run_id"] == interrupted["backfill_run_id"]
    assert resumed["status"] == "COMPLETED"
    assert resumed["processed_count"] == len(records)
    with get_connection() as connection:
        run_count = connection.execute(
            "SELECT COUNT(*) FROM backfill_runs"
        ).fetchone()[0]
    assert run_count == 1


def test_bad_record_does_not_block_other_records(
    backfill_database,
    tmp_path,
) -> None:
    records = _seed(include_bad=True, include_skip=False)

    report = run_backfill(
        BackfillOptions(
            apply=True,
            report_path=tmp_path / "partial",
        )
    )

    assert report["status"] == "PARTIAL"
    assert report["failed_count"] == 1
    assert report["success_count"] == len(records) - 1
    assert report["database_totals"]["canonical_market_count"] == 1
    assert report["failed_records"][0]["source_record_ids"] == [
        "bad-daily"
    ]


def test_conflict_is_retained_with_zero_confidence(
    backfill_database,
    tmp_path,
) -> None:
    _seed(include_conflict=True, include_skip=False)

    report = run_backfill(
        BackfillOptions(
            apply=True,
            report_path=tmp_path / "conflict",
        )
    )

    assert report["conflict_count"] == 1
    assert report["conflict_records"][0]["confidence"] == 0
    assert report["conflict_records"][0]["field_differences"]
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT confidence, field_differences_json
            FROM canonical_market_records
            WHERE verification_status = 'CONFLICT'
            """
        ).fetchone()
    assert row[0] == 0
    assert json.loads(row[1]) if isinstance(row[1], str) else row[1]


def test_reports_include_per_type_and_event_audit(
    backfill_database,
    tmp_path,
) -> None:
    _seed()

    report = run_backfill(
        BackfillOptions(
            apply=True,
            report_path=tmp_path / "audit",
        )
    )

    assert set(report["data_type_results"]) == {
        "announcement",
        "daily_bar",
        "financial_statement",
        "stock_basic",
    }
    assert report["status_counts"]["VERIFIED"] == 1
    assert report["status_counts"]["SINGLE_SOURCE"] == 1
    assert report["status_counts"]["SKIPPED"] == 1
    markdown = Path(report["report_markdown_path"]).read_text(
        encoding="utf-8"
    )
    assert "疑似重复事件" in markdown
    assert "隐藏思维链" in markdown


def test_filter_and_limit_are_applied_before_processing(
    backfill_database,
    tmp_path,
) -> None:
    _seed()

    report = run_backfill(
        BackfillOptions(
            data_types=(DataType.ANNOUNCEMENT.value,),
            limit=1,
            report_path=tmp_path / "filtered",
        )
    )

    assert report["selected_record_count"] == 1
    assert set(report["data_type_results"]) == {"announcement"}


def test_original_record_count_is_unchanged_after_backfill(
    backfill_database,
    tmp_path,
) -> None:
    records = _seed(include_conflict=True, include_bad=True)
    before = len(_raw_snapshot())

    run_backfill(
        BackfillOptions(
            apply=True,
            report_path=tmp_path / "preserve",
        )
    )

    assert before == len(records)
    assert len(_raw_snapshot()) == before
