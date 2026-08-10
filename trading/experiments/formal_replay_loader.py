from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from database.db import get_connection
from trading.experiments.formal_strategy_validation import (
    FormalReplayInput,
    HistoricalBarPoint,
)
from trading.experiments.historical_candidate_loader import HistoricalScanCandidate
from trading.experiments.parameter_sensitivity import FutureBar, REQUIRED_HORIZONS
from trading.research.fundamental.models import MarketValuationPoint
from trading.research.fundamental.repository import FundamentalRepository
from trading.scanner.production_partition import CandidateLayer
from trading.schemas import Bar


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
RISK_EVENT_LOOKBACK_DAYS = 30
TIMELINE_FORWARD_DAYS = 25


@dataclass(frozen=True)
class FormalReplayCoverage:
    candidate_count: int
    input_count: int
    symbols: int
    risk_event_covered_symbols: int
    historical_universe_complete: bool


def _cutoff(day: date) -> datetime:
    return datetime.combine(day, time(hour=16), tzinfo=SHANGHAI_TZ)


def load_candidate_file(path: Path) -> list[HistoricalScanCandidate]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        HistoricalScanCandidate(
            symbol=str(item["symbol"]),
            signal_date=date.fromisoformat(str(item["signal_date"])),
            layer=CandidateLayer(str(item["layer"])),
            price=float(item["price"]),
            amount=float(item["amount"]),
            technical_score=float(item["technical_score"]),
            sma20=float(item["sma20"]),
            sma60=float(item["sma60"]),
        )
        for item in payload
    ]


def _aligned_returns(
    future_dates: list[date],
    bars: dict[date, tuple[float, float]],
) -> tuple[dict[int, float], dict[date, float]]:
    if not future_dates or future_dates[0] not in bars:
        return {}, {}
    entry_open = bars[future_dates[0]][0]
    if entry_open <= 0:
        return {}, {}
    by_date = {
        item: bars[item][1] / entry_open - 1
        for item in future_dates
        if item in bars
    }
    return (
        {
            horizon: by_date[future_dates[horizon - 1]]
            for horizon in REQUIRED_HORIZONS
            if len(future_dates) >= horizon
            and future_dates[horizon - 1] in by_date
        },
        by_date,
    )


class FormalReplayInputLoader:
    """Read-only PIT loader for the exact formal replay service."""

    @staticmethod
    def _financial_records(connection: Any, symbol: str, cutoff: datetime):
        rows = connection.execute(
            """
            SELECT canonical_record_id, symbol, data_type,
                   report_period, announcement_time,
                   data_available_time, event_time, primary_source,
                   verification_status, source_record_ids_json,
                   confidence, statement_type, statement_version,
                   revision_of_record_id, accounting_scope,
                   period_type, source_type, disclosure_time_source,
                   payload_json
            FROM canonical_financial_records
            WHERE symbol = ?
              AND data_available_time IS NOT NULL
              AND data_available_time <= ?
              AND event_time <= ?
              AND verification_status <> 'CONFLICT'
            ORDER BY report_period DESC NULLS LAST,
                     statement_type,
                     data_available_time DESC,
                     canonical_record_id
            """,
            [symbol, cutoff, cutoff],
        ).fetchall()
        return tuple(FundamentalRepository._financial_record(row) for row in rows)

    @staticmethod
    def _history(connection: Any, symbol: str, signal_date: date, cutoff: datetime):
        rows = connection.execute(
            """
            SELECT bar_id, trade_date, open, high, low, close, volume,
                   data_available_time, data_cutoff
            FROM canonical_historical_bars
            WHERE symbol = ?
              AND trade_date <= ?
              AND adjustment_type = 'RAW'
              AND verification_status <> 'CONFLICT'
              AND data_available_time <= ?
              AND data_cutoff <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY trade_date
                ORDER BY generated_at DESC, bar_id
            ) = 1
            ORDER BY trade_date DESC
            LIMIT 60
            """,
            [symbol, signal_date, cutoff, cutoff],
        ).fetchall()
        points = [
            HistoricalBarPoint(
                bar=Bar(
                    trade_date=row[1],
                    open=float(row[2]), high=float(row[3]),
                    low=float(row[4]), close=float(row[5]),
                    volume=float(row[6]),
                ),
                data_available_time=row[7],
                data_cutoff=row[8],
            )
            for row in reversed(rows)
        ]
        valuation = None
        if rows:
            latest = rows[0]
            valuation = MarketValuationPoint(
                canonical_record_id=str(latest[0]),
                source_record_ids=[str(latest[0])],
                event_time=cutoff,
                close=float(latest[5]),
            )
        return tuple(points), valuation

    @staticmethod
    def _market_context(connection: Any):
        rows = connection.execute(
            """
            SELECT trade_date, open, close
            FROM historical_benchmark_bars
            WHERE benchmark_type = 'CSI300'
              AND benchmark_code = '000300.SH'
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY trade_date
                ORDER BY fetched_at DESC, bar_id
            ) = 1
            ORDER BY trade_date
            """
        ).fetchall()
        return {row[0]: (float(row[1]), float(row[2])) for row in rows}

    @staticmethod
    def _future(
        connection: Any,
        symbol: str,
        signal_date: date,
        future_dates: list[date],
        signal_close: float,
    ) -> tuple[FutureBar, ...]:
        if not future_dates:
            return ()
        rows = connection.execute(
            """
            WITH bars AS (
                SELECT trade_date, open, high, low, close, volume
                FROM canonical_historical_bars
                WHERE symbol = ?
                  AND adjustment_type = 'RAW'
                  AND verification_status <> 'CONFLICT'
                  AND trade_date IN (SELECT UNNEST(?::DATE[]))
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY trade_date
                    ORDER BY generated_at DESC, bar_id
                ) = 1
            ), factors AS (
                SELECT trade_date, adj_factor
                FROM historical_adjustment_factors
                WHERE symbol = ?
                  AND trade_date BETWEEN ? AND ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY trade_date
                    ORDER BY fetched_at DESC, factor_id
                ) = 1
            )
            SELECT b.trade_date, b.open, b.high, b.low, b.close, b.volume,
                   f.adj_factor
            FROM bars b
            LEFT JOIN factors f USING (trade_date)
            ORDER BY b.trade_date
            """,
            [symbol, future_dates, symbol, signal_date, future_dates[-1]],
        ).fetchall()
        by_date = {row[0]: row[1:] for row in rows}
        output: list[FutureBar] = []
        previous_close = signal_close
        for day in future_dates:
            row = by_date.get(day)
            if row is None:
                output.append(
                    FutureBar(
                        trade_date=day, open=None, high=None, low=None,
                        close=None, previous_close=previous_close,
                        suspended=True,
                    )
                )
                continue
            open_, high, low, close, volume, factor = row
            output.append(
                FutureBar(
                    trade_date=day,
                    open=float(open_), high=float(high), low=float(low),
                    close=float(close), previous_close=previous_close,
                    volume=float(volume), suspended=False,
                    adjustment_factor=(None if factor is None else float(factor)),
                )
            )
            previous_close = float(close)
        return tuple(output)

    @staticmethod
    def _industry_returns(
        connection: Any,
        symbol: str,
        signal_date: date,
        cutoff: datetime,
        future_dates: list[date],
    ) -> dict[date, float]:
        membership = connection.execute(
            """
            SELECT industry_code
            FROM historical_industry_memberships
            WHERE symbol = ?
              AND valid_from <= ?
              AND (valid_to IS NULL OR valid_to > ?)
              AND data_available_time <= ?
              AND data_cutoff <= ?
            ORDER BY valid_from DESC, membership_id
            LIMIT 1
            """,
            [symbol, signal_date, signal_date, cutoff, cutoff],
        ).fetchone()
        if membership is None or not future_dates:
            return {}
        rows = connection.execute(
            """
            SELECT trade_date, open, close
            FROM historical_benchmark_bars
            WHERE benchmark_type = 'INDUSTRY'
              AND benchmark_code = ?
              AND trade_date IN (SELECT UNNEST(?::DATE[]))
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY trade_date
                ORDER BY fetched_at DESC, bar_id
            ) = 1
            ORDER BY trade_date
            """,
            [membership[0], future_dates],
        ).fetchall()
        bars = {row[0]: (float(row[1]), float(row[2])) for row in rows}
        return _aligned_returns(future_dates, bars)[1]

    @staticmethod
    def _statuses(
        connection: Any,
        symbol: str,
        signal_date: date,
        cutoff: datetime,
    ) -> tuple[bool, bool, bool, bool]:
        universe = connection.execute(
            """
            SELECT list_date, delist_date
            FROM stock_universe
            WHERE symbol = ?
            LIMIT 1
            """,
            [symbol],
        ).fetchone()
        listed = bool(
            universe is not None
            and universe[0] is not None
            and universe[0] <= signal_date
            and (universe[1] is None or universe[1] >= signal_date)
        )
        delisted = bool(universe and universe[1] is not None and universe[1] <= signal_date)
        kinds = {
            row[0]
            for row in connection.execute(
                """
                SELECT status_type
                FROM historical_security_statuses
                WHERE symbol = ?
                  AND status_value = TRUE
                  AND effective_start <= ?
                  AND (effective_end IS NULL OR effective_end > ?)
                  AND data_available_time <= ?
                  AND data_cutoff <= ?
                """,
                [symbol, signal_date, signal_date, cutoff, cutoff],
            ).fetchall()
        }
        return "SUSPENDED" in kinds, delisted or "DELISTED" in kinds, "ST" in kinds, listed

    @staticmethod
    def _risk_titles(
        connection: Any,
        symbol: str,
        cutoff: datetime,
    ) -> tuple[str, ...]:
        start = cutoff - timedelta(days=RISK_EVENT_LOOKBACK_DAYS)
        return tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT title
                FROM historical_risk_events
                WHERE symbol = ?
                  AND data_available_time > ?
                  AND data_available_time < DATE_TRUNC('day', ?)
                  AND data_cutoff < DATE_TRUNC('day', ?)
                ORDER BY data_available_time, event_id
                """,
                [symbol, start, cutoff, cutoff],
            ).fetchall()
        )

    def load(
        self,
        candidates: Iterable[HistoricalScanCandidate],
        *,
        risk_event_covered_symbols: set[str],
    ) -> tuple[list[FormalReplayInput], FormalReplayCoverage]:
        items = list(candidates)
        market_by_date: dict[date, tuple[float, float]]
        output: list[FormalReplayInput] = []
        universe_complete = True
        with get_connection(read_only=True) as connection:
            market_by_date = self._market_context(connection)
            market_dates = sorted(market_by_date)
            market_index = {day: index for index, day in enumerate(market_dates)}
            for candidate in items:
                cutoff = _cutoff(candidate.signal_date)
                index = market_index.get(candidate.signal_date)
                if index is None or index + 20 >= len(market_dates):
                    continue
                future_dates = market_dates[
                    index + 1:index + 1 + TIMELINE_FORWARD_DAYS
                ]
                history, valuation = self._history(
                    connection, candidate.symbol, candidate.signal_date, cutoff
                )
                if len(history) < 60 or valuation is None:
                    continue
                future = self._future(
                    connection, candidate.symbol, candidate.signal_date,
                    future_dates, history[-1].bar.close,
                )
                benchmark, benchmark_by_date = _aligned_returns(
                    future_dates, market_by_date
                )
                trailing = (
                    market_by_date[candidate.signal_date][1]
                    / market_by_date[market_dates[index - 20]][1]
                    - 1
                ) if index >= 20 else 0.0
                regime = (
                    "RISING" if trailing >= 0.05
                    else "FALLING" if trailing <= -0.05
                    else "SIDEWAYS"
                )
                suspended, delisted, st_status, listed = self._statuses(
                    connection, candidate.symbol, candidate.signal_date, cutoff
                )
                universe_complete = universe_complete and listed
                output.append(
                    FormalReplayInput(
                        symbol=candidate.symbol,
                        signal_date=candidate.signal_date,
                        data_cutoff=cutoff,
                        layer=candidate.layer,
                        bars=history,
                        financial_records=self._financial_records(
                            connection, candidate.symbol, cutoff
                        ),
                        valuation_point=valuation,
                        future_bars=future,
                        csi300_returns=benchmark,
                        csi300_returns_by_exit_date=benchmark_by_date,
                        industry_returns_by_exit_date=self._industry_returns(
                            connection, candidate.symbol, candidate.signal_date,
                            cutoff, future_dates,
                        ),
                        market_regime=regime,
                        risk_event_titles=self._risk_titles(
                            connection, candidate.symbol, cutoff
                        ),
                        risk_event_coverage_complete=(
                            candidate.symbol in risk_event_covered_symbols
                        ),
                        suspended=suspended,
                        delisted=delisted,
                        st_status=st_status,
                        historical_universe_complete=listed,
                    )
                )
        covered = len({item.symbol for item in items} & risk_event_covered_symbols)
        return output, FormalReplayCoverage(
            candidate_count=len(items),
            input_count=len(output),
            symbols=len({item.symbol for item in items}),
            risk_event_covered_symbols=covered,
            historical_universe_complete=universe_complete,
        )


__all__ = [
    "FormalReplayCoverage",
    "FormalReplayInputLoader",
    "RISK_EVENT_LOOKBACK_DAYS",
    "TIMELINE_FORWARD_DAYS",
    "load_candidate_file",
]