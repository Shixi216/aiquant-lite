"""Read-only repository for canonical RAW bars and adjustment factors.

Deliberately opens the database read-only: this module backs a GET-only
Router endpoint and must never create tables or migrate.
"""
from __future__ import annotations

from datetime import date
import duckdb

from database.db import get_connection


class HistoricalBarsRepository:
    """Fetch deduplicated RAW bars and matching adjustment factors."""

    _BAR_SQL = """
        SELECT symbol, trade_date, open, high, low, close, volume, amount,
               data_cutoff
        FROM canonical_historical_bars
        WHERE adjustment_type = 'RAW'
          AND verification_status <> 'CONFLICT'
          AND symbol IN (SELECT UNNEST(?::VARCHAR[]))
          AND trade_date <= ?
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY symbol, trade_date
            ORDER BY generated_at DESC, bar_id
        ) = 1
        ORDER BY trade_date DESC
        LIMIT ?
    """

    _FACTOR_SQL = """
        SELECT symbol, trade_date, adj_factor
        FROM historical_adjustment_factors
        WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
          AND trade_date <= ?
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY symbol, trade_date
            ORDER BY fetched_at DESC, factor_id
        ) = 1
        ORDER BY trade_date
    """

    def fetch_raw_bars(
        self,
        *,
        symbols: list[str],
        end_date: date,
        limit: int,
    ) -> list[tuple]:
        with get_connection(read_only=True) as connection:
            try:
                rows = connection.execute(
                    self._BAR_SQL,
                    [symbols, end_date, limit],
                ).fetchall()
            except duckdb.CatalogException:
                return []
        return list(reversed(rows))

    def fetch_adjustment_factors(
        self,
        *,
        symbols: list[str],
        end_date: date,
    ) -> dict[date, float]:
        with get_connection(read_only=True) as connection:
            try:
                rows = connection.execute(
                    self._FACTOR_SQL,
                    [symbols, end_date],
                ).fetchall()
            except duckdb.CatalogException:
                return {}
        return {row[1]: float(row[2]) for row in rows}


__all__ = ["HistoricalBarsRepository"]
