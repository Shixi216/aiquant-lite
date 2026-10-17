"""Server-side full-universe aggregation over the latest market snapshot.

Deliberately avoids the ``limit <= 5000`` item-list path: the universe is
~5549 symbols, so the aggregate is computed in SQL over *all* items of the
latest run instead of fetching rows into the WebUI.
"""
from __future__ import annotations

from datetime import date
import duckdb

from database.db import get_connection

_AGGREGATE_SQL = """
    SELECT
        COUNT(*) FILTER (WHERE change_pct IS NOT NULL AND change_pct > 0)
            AS advances,
        COUNT(*) FILTER (WHERE change_pct IS NOT NULL AND change_pct < 0)
            AS declines,
        COUNT(*) FILTER (WHERE change_pct = 0)
            AS flats,
        COUNT(*) FILTER (WHERE change_pct > 7) AS gt7,
        COUNT(*) FILTER (WHERE change_pct > 5 AND change_pct <= 7) AS p5_7,
        COUNT(*) FILTER (WHERE change_pct > 2 AND change_pct <= 5) AS p2_5,
        COUNT(*) FILTER (WHERE change_pct > 0 AND change_pct <= 2) AS p0_2,
        COUNT(*) FILTER (WHERE change_pct >= -2 AND change_pct < 0) AS n0_2,
        COUNT(*) FILTER (WHERE change_pct >= -5 AND change_pct < -2) AS n2_5,
        COUNT(*) FILTER (WHERE change_pct >= -7 AND change_pct < -5) AS n5_7,
        COUNT(*) FILTER (WHERE change_pct < -7) AS lt7,
        SUM(amount * CASE
            WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'CNY' THEN 1
            WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'THOUSAND_CNY' THEN 1000
            WHEN COALESCE(TRY(json_extract_string(payload_json, '$.amount_unit')), '') = 'TEN_THOUSAND_CNY' THEN 10000
            -- Legacy scanner snapshots preserve provider-native units.
            WHEN source = 'Tushare' THEN 1000
            WHEN source = 'Tencent' THEN 10000
            ELSE 1
        END) AS total_amount,
        MEDIAN(change_pct) AS median_change_pct
    FROM market_snapshot_items
    WHERE snapshot_id = ?
"""

_RUN_SQL = """
    SELECT snapshot_id, data_cutoff, expected_universe_size,
           received_symbol_count, valid_symbol_count, coverage_ratio,
           completeness_status, snapshot_time
    FROM market_snapshot_runs
    WHERE mode = 'APPLY' AND completeness_status != 'FAILED'
    ORDER BY snapshot_time DESC, completed_at DESC
    LIMIT 1
"""

_BARS_MAX_DATE_SQL = "SELECT MAX(trade_date) FROM canonical_historical_bars"

_SNAPSHOT_TRADE_DATE_SQL = """
    SELECT MAX(COALESCE(
        TRY_STRPTIME(TRY(json_extract_string(payload_json, '$.trade_date')), '%Y%m%d')::DATE,
        TRY_CAST(TRY(json_extract_string(payload_json, '$.trade_date')) AS DATE)
    ))
    FROM market_snapshot_items
    WHERE snapshot_id = ?
"""


class MarketBreadthRepository:
    """Aggregate one full snapshot run plus the canonical bars max date."""

    def latest_breadth(
        self,
    ) -> tuple[tuple, dict, date | None, date | None] | None:
        with get_connection(read_only=True) as connection:
            try:
                run = connection.execute(_RUN_SQL).fetchone()
            except duckdb.CatalogException:
                return None
            if run is None:
                return None
            aggregates = connection.execute(_AGGREGATE_SQL, [run[0]]).fetchone()
            snapshot_trade_date = self._snapshot_trade_date(connection, run[0])
            bars_max_trade_date = self._bars_max_trade_date(connection)
        return (
            run,
            self._to_dict(aggregates),
            snapshot_trade_date,
            bars_max_trade_date,
        )

    @staticmethod
    def _snapshot_trade_date(connection, snapshot_id: str) -> date | None:
        try:
            row = connection.execute(
                _SNAPSHOT_TRADE_DATE_SQL, [snapshot_id]
            ).fetchone()
        except duckdb.CatalogException:
            return None
        return row[0] if row else None

    @staticmethod
    def _bars_max_trade_date(connection) -> date | None:
        try:
            row = connection.execute(_BARS_MAX_DATE_SQL).fetchone()
        except duckdb.CatalogException:
            return None
        return row[0] if row else None

    @staticmethod
    def _to_dict(row: tuple) -> dict:
        keys = [
            "advances", "declines", "flats",
            "gt7", "p5_7", "p2_5", "p0_2",
            "n0_2", "n2_5", "n5_7", "lt7",
            "total_amount", "median_change_pct",
        ]
        return dict(zip(keys, row))


__all__ = ["MarketBreadthRepository"]
