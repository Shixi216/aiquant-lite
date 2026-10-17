from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import duckdb
import pandas as pd
from fastapi.testclient import TestClient

from config.settings import settings
from data_hub.services.trading_calendar_service import TradingCalendarService
from database.migrations.v0106_full_market_data_foundation import (
    apply_migration as apply_v0106,
)
from database.migrations.v0107_historical_market_expansion import (
    apply_migration as apply_v0107,
)
from router.api.app import app

client = TestClient(app)

ITEM_COLUMNS = [
    "snapshot_id", "symbol", "price", "previous_close", "open", "high", "low",
    "volume", "amount", "change", "change_pct", "turnover_rate",
    "snapshot_time", "source", "item_status", "is_suspended", "is_abnormal",
    "raw_record_id", "payload_json",
]


def _prepare(tmp_path, monkeypatch) -> duckdb.DuckDBPyConnection:
    db_path = tmp_path / "breadth.duckdb"
    monkeypatch.setattr(settings, "opc_database_path", db_path)
    connection = duckdb.connect(str(db_path))
    apply_v0106(connection)
    apply_v0107(connection)
    return connection


def _insert_run(
    connection: duckdb.DuckDBPyConnection,
    snapshot_id: str,
    *,
    expected: int,
    received: int,
    completeness_status: str,
    snapshot_time: datetime,
) -> None:
    connection.execute(
        """
        INSERT INTO market_snapshot_runs (
            snapshot_id, mode, provider, data_cutoff, expected_universe_size,
            received_symbol_count, valid_symbol_count, missing_symbol_count,
            coverage_ratio, completeness_status, started_at, completed_at,
            snapshot_time, content_hash, request_count, elapsed_seconds,
            peak_memory_bytes, database_growth_bytes, error_message
        ) VALUES (
            ?, 'APPLY', 'Tushare Pro', ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, 'hash', 1, 1.0,
            NULL, NULL, NULL
        )
        """,
        [
            snapshot_id,
            snapshot_time,
            expected,
            received,
            received,
            expected - received,
            received / expected,
            completeness_status,
            snapshot_time - timedelta(minutes=1),
            snapshot_time,
            snapshot_time,
        ],
    )


def _insert_items(
    connection: duckdb.DuckDBPyConnection,
    snapshot_id: str,
    change_pcts: list[float | None],
    *,
    amount: float = 100_000_000.0,
    snapshot_time: datetime | None = None,
    trade_date: str | None = None,
) -> None:
    rows = []
    for index, change_pct in enumerate(change_pcts):
        snapshot_time = snapshot_time or datetime(2026, 9, 4, 23, 44)
        rows.append(
            [
                snapshot_id,
                f"{index:06d}.SZ",
                10.0,
                10.0,
                10.0,
                10.5,
                9.8,
                1_000_000.0,
                amount,
                0.1 if change_pct is not None else None,
                change_pct,
                1.5,
                snapshot_time,
                "Tushare Pro",
                "OK",
                change_pct is None,
                False,
                f"raw-{index}",
                f'{{"trade_date":"{trade_date}"}}' if trade_date else "{}",
            ]
        )
    frame = pd.DataFrame.from_records(rows, columns=ITEM_COLUMNS)
    connection.register("_breadth_items", frame)
    try:
        connection.execute(
            f"INSERT INTO market_snapshot_items ({', '.join(ITEM_COLUMNS)}) "
            "SELECT {cols} FROM _breadth_items".format(
                cols=", ".join(ITEM_COLUMNS)
            )
        )
    finally:
        connection.unregister("_breadth_items")


def _fake_calendar(monkeypatch) -> None:
    monkeypatch.setattr(
        TradingCalendarService,
        "latest_completed_trade_date",
        lambda self, **kwargs: (
            kwargs.get("data_available_date") or date(2026, 9, 3),
            "FRESH",
        ),
    )


def test_breadth_aggregates_over_5000_items(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    snapshot_time = datetime(2026, 9, 4, 23, 44, tzinfo=timezone.utc)
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return snapshot_time + timedelta(seconds=60)

    monkeypatch.setattr("data_hub.services.market_breadth_service.datetime", FrozenDateTime)
    # 5020 items -> proves the le=5000 item path is not used
    change_pcts = [7.5] * 10 + [6.0] * 20 + [3.0] * 40 + [1.0] * 100
    change_pcts += [0.0] * 50 + [-1.0] * 300 + [-3.0] * 200 + [-6.0] * 100
    change_pcts += [-7.5] * 20 + [None] * 4180
    assert len(change_pcts) == 5020
    _insert_run(
        connection,
        "snap-1",
        expected=5020,
        received=5020,
        completeness_status="COMPLETE",
        snapshot_time=snapshot_time,
    )
    _insert_items(connection, "snap-1", change_pcts, trade_date="20260904")
    # bars max trade date
    event = datetime(2026, 9, 3, 0, 0)
    connection.execute(
        """
        INSERT INTO canonical_historical_bars (
            bar_id, symbol, trade_date, event_time, data_available_time,
            data_cutoff, generated_at, adjustment_type, open, high, low, close,
            volume, amount, volume_unit, amount_unit, primary_source,
            source_record_ids_json, verification_source_ids_json,
            verification_status, confidence, content_hash, algorithm_version,
            raw_payload_json
        ) VALUES ('bar-1', '600519.SH', '2026-09-03', ?, ?, ?, ?,
                  'RAW', 1, 1, 1, 1, 1, 1, 'SHARE', 'CNY', 'Tushare Pro',
                  '[]', '[]', 'SINGLE_SOURCE', 1.0, 'h', 'v1', '{}')
        """,
        [event, event, event, event],
    )
    connection.close()

    _fake_calendar(monkeypatch)
    response = client.get("/v1/market-snapshots/latest/breadth")
    assert response.status_code == 200
    body = response.json()
    assert body["snapshot_id"] == "snap-1"
    assert body["advances"] == 170
    assert body["declines"] == 620
    assert body["flats"] == 50
    assert body["change_buckets"]["gt7"] == 10
    assert body["change_buckets"]["p5_7"] == 20
    assert body["change_buckets"]["p2_5"] == 40
    assert body["change_buckets"]["p0_2"] == 100
    assert body["change_buckets"]["flat"] == 50
    assert body["change_buckets"]["n0_2"] == 300
    assert body["change_buckets"]["n2_5"] == 200
    assert body["change_buckets"]["n5_7"] == 100
    assert body["change_buckets"]["lt7"] == 20
    assert body["total_amount"] == 5020 * 100_000_000.0
    # median over non-null change_pct (820 values) = -1.0
    assert body["median_change_pct"] == -1.0
    assert body["limit_ups"] is None and body["limit_downs"] is None
    assert "not guessed" in body["limit_counts_derivation"]
    # The snapshot's own trade_date is the availability bound. Historical bars
    # may lag independently and must not downgrade a complete market snapshot.
    assert body["market_trade_date"] == "2026-09-04"
    assert body["market_trade_date_status"] == "FRESH"
    assert (
        "latest_completed_trade_date" in body["market_trade_date_derivation"]
    )
    assert body["bars_max_trade_date"] == "2026-09-03"
    assert body["snapshot_data_trade_date"] == "2026-09-04"
    assert body["partial_universe"] is False
    assert body["stale"] is False


def test_breadth_partial_universe_flag(tmp_path, monkeypatch) -> None:
    connection = _prepare(tmp_path, monkeypatch)
    snapshot_time = datetime(2026, 9, 4, 23, 44, tzinfo=timezone.utc)
    _insert_run(
        connection,
        "snap-2",
        expected=5549,
        received=5000,
        completeness_status="PARTIAL",
        snapshot_time=snapshot_time,
    )
    _insert_items(
        connection,
        "snap-2",
        [1.0, -1.0],
        snapshot_time=snapshot_time,
    )
    connection.close()

    _fake_calendar(monkeypatch)
    response = client.get("/v1/market-snapshots/latest/breadth")
    assert response.status_code == 200
    body = response.json()
    assert body["partial_universe"] is True
    assert body["expected_universe_size"] == 5549
    assert body["received_symbol_count"] == 5000


def test_breadth_no_snapshot_404(tmp_path, monkeypatch) -> None:
    _prepare(tmp_path, monkeypatch)
    response = client.get("/v1/market-snapshots/latest/breadth")
    assert response.status_code == 404


def test_breadth_normalizes_legacy_snapshot_amount_units(tmp_path, monkeypatch):
    from data_hub.repositories.market_breadth import MarketBreadthRepository
    connection = _prepare(tmp_path, monkeypatch)
    _insert_run(connection, "units", expected=4, received=4,
                completeness_status="COMPLETE", snapshot_time=datetime.now(timezone.utc))
    _insert_items(connection, "units", [1.0] * 4, amount=2.0)
    connection.execute("UPDATE market_snapshot_items SET source='Tushare' WHERE symbol='000000.SZ'")
    connection.execute("UPDATE market_snapshot_items SET source='Tencent' WHERE symbol='000001.SZ'")
    connection.execute("""UPDATE market_snapshot_items SET source='Tushare',
        payload_json='{"amount_unit":"CNY"}' WHERE symbol='000002.SZ' """)
    connection.close()
    _run, aggregate, _snapshot_date, _bars_date = MarketBreadthRepository().latest_breadth()
    assert aggregate["total_amount"] == 2 * 1000 + 2 * 10000 + 2 + 2
