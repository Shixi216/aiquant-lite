"""Local sidecar bars: whitelisted DuckDB files opened READ-ONLY.

Hard rules (enforced by tests/test_webui_v2.py static gate):
- explicit path constants from webui.config only; never the main database
- ``duckdb.connect(..., read_only=True)`` only
- no initialize_database / migrations / CREATE / INSERT / UPDATE / DELETE
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from webui.config import BACKUP_DB_PATH, ENABLE_BACKUP_BAR_FALLBACK, REPLAY_DB_PATH
from webui.providers.symbol import canonical_candidates

_BAR_COLUMNS = [
    "symbol", "trade_date", "open", "high", "low", "close",
    "volume", "amount", "adj_factor",
]

# 全市场稠密覆盖的最小标的数（B0 实测：稠密覆盖止于 2026-08-06 / 5524 只）
_DENSE_COVERAGE_MIN_SYMBOLS = 1000

# Whitelist frozen at import: later monkeypatching of config paths must not
# be able to redirect these reads to the main database.
_WHITELIST = frozenset(
    {
        Path(REPLAY_DB_PATH).resolve(),
        Path(BACKUP_DB_PATH).resolve(),
    }
)


def _assert_whitelisted(db_path: str) -> None:
    """Refuse any database file that is not an explicit whitelist constant."""
    if Path(db_path).resolve() not in _WHITELIST:
        raise PermissionError(
            f"path not whitelisted for WebUI sidecar reads: {db_path}"
        )


@dataclass(frozen=True)
class LocalBarsResult:
    symbol: str | None
    bars: list[dict] = field(default_factory=list)
    last_trade_date: str | None = None


def _query_replay(
    db_path: str,
    candidates: list[str],
    limit: int,
) -> list[tuple]:
    _assert_whitelisted(db_path)
    connection = duckdb.connect(db_path, read_only=True)
    try:
        rows = connection.execute(
            f"""
            SELECT {', '.join(_BAR_COLUMNS)}
            FROM replay_price_bars
            WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, trade_date
                ORDER BY trade_date DESC
            ) = 1
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            [candidates, limit],
        ).fetchall()
    finally:
        connection.close()
    return rows


def fetch_local_raw_bars(
    symbol: str,
    *,
    limit: int = 400,
) -> LocalBarsResult:
    """Most recent RAW bars with their adjustment factors from the sidecar."""
    candidates = canonical_candidates(symbol)
    if not candidates:
        return LocalBarsResult(symbol=None)
    _assert_whitelisted(str(REPLAY_DB_PATH))
    try:
        rows = _query_replay(str(REPLAY_DB_PATH), candidates, limit)
    except (duckdb.Error, OSError):
        rows = []
    if not rows and ENABLE_BACKUP_BAR_FALLBACK:
        rows = _query_backup(str(BACKUP_DB_PATH), candidates, limit)
    # _query_* returns most-recent-first; the chain needs ascending order
    rows = list(reversed(rows))
    bars = [
        {
            "trade_date": row[1].isoformat() if hasattr(row[1], "isoformat") else str(row[1]),
            "open": row[2],
            "high": row[3],
            "low": row[4],
            "close": row[5],
            "volume": row[6],
            "amount": row[7],
            "adj_factor": row[8],
        }
        for row in rows
    ]
    matched_symbol = rows[0][0] if rows else None
    last = max((bar["trade_date"] for bar in bars), default=None)
    return LocalBarsResult(symbol=matched_symbol, bars=bars, last_trade_date=last)


def _query_backup(
    db_path: str,
    candidates: list[str],
    limit: int,
) -> list[tuple]:
    """Temporary backup fallback (opt-in only; sparse recent tail)."""
    _assert_whitelisted(db_path)
    connection = duckdb.connect(db_path, read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT symbol, trade_date, open, high, low, close, volume, amount,
                   NULL AS adj_factor
            FROM canonical_historical_bars
            WHERE adjustment_type = 'RAW'
              AND symbol IN (SELECT UNNEST(?::VARCHAR[]))
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY symbol, trade_date
                ORDER BY generated_at DESC, bar_id
            ) = 1
            ORDER BY trade_date DESC
            LIMIT ?
            """,
            [candidates, limit],
        ).fetchall()
    except duckdb.CatalogException:
        return []
    finally:
        connection.close()
    return rows


def full_market_coverage_date() -> str | None:
    """Latest trade date with dense (>threshold) all-market coverage."""
    _assert_whitelisted(str(REPLAY_DB_PATH))
    try:
        connection = duckdb.connect(str(REPLAY_DB_PATH), read_only=True)
    except (duckdb.Error, OSError):
        return None
    try:
        row = connection.execute(
            """
            SELECT trade_date
            FROM (
                SELECT trade_date, COUNT(DISTINCT symbol) AS symbol_count
                FROM replay_price_bars
                GROUP BY trade_date
                HAVING COUNT(DISTINCT symbol) >= ?
            )
            ORDER BY trade_date DESC
            LIMIT 1
            """,
            [_DENSE_COVERAGE_MIN_SYMBOLS],
        ).fetchone()
    except duckdb.Error:
        return None
    finally:
        connection.close()
    if row is None:
        return None
    return row[0].isoformat() if hasattr(row[0], "isoformat") else str(row[0])


def backup_fallback_enabled() -> bool:
    return ENABLE_BACKUP_BAR_FALLBACK and BACKUP_DB_PATH.is_file()


__all__ = [
    "LocalBarsResult",
    "fetch_local_raw_bars",
    "full_market_coverage_date",
    "backup_fallback_enabled",
]
