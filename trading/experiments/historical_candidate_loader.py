from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from statistics import fmean

import pandas as pd

from database.db import get_connection
from trading.research.technical.analysis import technical_signal
from trading.scanner.production_partition import (
    CandidateLayer,
    classify_daily_candidate,
)
from trading.schemas import Bar


CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class HistoricalScanCandidate:
    symbol: str
    signal_date: date
    layer: CandidateLayer
    price: float
    amount: float
    technical_score: float
    sma20: float
    sma60: float


def eligible_signal_dates(
    market_dates: list[date],
    *,
    history_days: int = 60,
    forward_days: int = 20,
) -> list[date]:
    if len(market_dates) < history_days + forward_days + 1:
        return []
    return market_dates[history_days - 1 : -forward_days]


def month_end_signal_dates(
    market_dates: list[date],
    *,
    history_days: int = 60,
    forward_days: int = 20,
) -> list[date]:
    eligible = eligible_signal_dates(
        market_dates,
        history_days=history_days,
        forward_days=forward_days,
    )
    by_month: dict[tuple[int, int], date] = {}
    for item in eligible:
        by_month[(item.year, item.month)] = item
    return [by_month[key] for key in sorted(by_month)]


class HistoricalCandidateLoader:
    def load_daily(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[HistoricalScanCandidate]:
        return self._load(
            start_date=start_date,
            end_date=end_date,
            use_all_eligible_dates=True,
        )

    def load_month_end(
        self,
        *,
        start_date: date,
        end_date: date,
    ) -> list[HistoricalScanCandidate]:
        return self._load(
            start_date=start_date,
            end_date=end_date,
            use_all_eligible_dates=False,
        )

    def _load(
        self,
        *,
        start_date: date,
        end_date: date,
        use_all_eligible_dates: bool,
    ) -> list[HistoricalScanCandidate]:
        with get_connection(read_only=True) as connection:
            market_dates = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT trade_date
                    FROM canonical_historical_bars
                    WHERE adjustment_type = 'RAW'
                      AND verification_status <> 'CONFLICT'
                      AND trade_date BETWEEN ? AND ?
                    GROUP BY trade_date
                    HAVING COUNT(DISTINCT symbol) >= 5000
                    ORDER BY trade_date
                    """,
                    [start_date, end_date],
                ).fetchall()
            ]
            signal_dates = (
                eligible_signal_dates(market_dates)
                if use_all_eligible_dates
                else month_end_signal_dates(market_dates)
            )
            if not signal_dates:
                return []
            coarse = connection.execute(
                """
                WITH bars AS (
                    SELECT symbol, trade_date, close, amount,
                           data_available_time, data_cutoff,
                           LAG(close) OVER (
                               PARTITION BY symbol ORDER BY trade_date
                           ) AS previous_close
                    FROM canonical_historical_bars
                    WHERE adjustment_type = 'RAW'
                      AND verification_status <> 'CONFLICT'
                      AND trade_date <= ?
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY symbol, trade_date
                        ORDER BY generated_at DESC, bar_id
                    ) = 1
                ), eligible AS (
                    SELECT b.symbol, b.trade_date, b.close, b.amount,
                           ROW_NUMBER() OVER (
                               PARTITION BY b.trade_date
                               ORDER BY b.amount DESC, b.symbol
                           ) AS amount_rank
                    FROM bars b
                    JOIN stock_universe u ON u.symbol = b.symbol
                    WHERE b.trade_date IN (
                        SELECT UNNEST(?::DATE[])
                    )
                      AND u.list_date <= b.trade_date
                      AND (
                          u.delist_date IS NULL
                          OR u.delist_date >= b.trade_date
                      )
                      AND b.data_available_time <= CAST(b.trade_date AS TIMESTAMP)
                          + INTERVAL '16 hours'
                      AND b.data_cutoff <= CAST(b.trade_date AS TIMESTAMP)
                          + INTERVAL '16 hours'
                      AND b.previous_close > 0
                      AND (b.close / b.previous_close - 1) > 0
                      AND b.close BETWEEN 10 AND 200
                      AND b.amount >= 300000000
                      AND NOT EXISTS (
                          SELECT 1
                          FROM historical_security_statuses s
                          WHERE s.symbol = b.symbol
                            AND s.status_type = 'SUSPENDED'
                            AND s.status_value = TRUE
                            AND s.effective_start <= b.trade_date
                            AND (
                                s.effective_end IS NULL
                                OR s.effective_end > b.trade_date
                            )
                            AND s.data_available_time <= CAST(b.trade_date AS TIMESTAMP)
                                + INTERVAL '16 hours'
                      )
                )
                SELECT symbol, trade_date, close, amount
                FROM eligible
                WHERE amount_rank <= 30
                ORDER BY trade_date, amount_rank, symbol
                """,
                [signal_dates[-1], signal_dates],
            ).fetchall()
            if not coarse:
                return []
            pairs = pd.DataFrame.from_records(
                coarse,
                columns=["symbol", "signal_date", "price", "amount"],
            )
            connection.register("_formal_candidate_pairs", pairs)
            try:
                history = connection.execute(
                    """
                    SELECT p.signal_date, p.symbol, b.trade_date,
                           b.open, b.high, b.low, b.close, b.volume
                    FROM _formal_candidate_pairs p
                    JOIN canonical_historical_bars b
                      ON b.symbol = p.symbol
                     AND b.trade_date <= p.signal_date
                    WHERE b.adjustment_type = 'RAW'
                      AND b.verification_status <> 'CONFLICT'
                      AND b.data_available_time <= CAST(b.trade_date AS TIMESTAMP)
                          + INTERVAL '16 hours'
                      AND b.data_cutoff <= CAST(b.trade_date AS TIMESTAMP)
                          + INTERVAL '16 hours'
                    QUALIFY ROW_NUMBER() OVER (
                        PARTITION BY p.signal_date, p.symbol, b.trade_date
                        ORDER BY b.generated_at DESC, b.bar_id
                    ) = 1
                    AND ROW_NUMBER() OVER (
                        PARTITION BY p.signal_date, p.symbol
                        ORDER BY b.trade_date DESC, b.generated_at DESC, b.bar_id
                    ) <= 60
                    ORDER BY p.signal_date, p.symbol, b.trade_date
                    """
                ).fetchall()
            finally:
                connection.unregister("_formal_candidate_pairs")
        grouped: dict[tuple[date, str], list[Bar]] = defaultdict(list)
        for signal_date, symbol, trade_date, open_, high, low, close, volume in history:
            grouped[(signal_date, symbol)].append(
                Bar(
                    trade_date=trade_date,
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(volume),
                )
            )
        context = {
            (signal_date, symbol): (float(price), float(amount))
            for symbol, signal_date, price, amount in coarse
        }
        output: list[HistoricalScanCandidate] = []
        for key, bars in sorted(grouped.items()):
            if len(bars) < 60:
                continue
            ordered = sorted(bars, key=lambda item: item.trade_date)
            closes = [item.close for item in ordered]
            score = technical_signal(ordered).score
            sma20 = fmean(closes[-20:])
            sma60 = fmean(closes[-60:])
            price, amount = context[key]
            output.append(
                HistoricalScanCandidate(
                    symbol=key[1],
                    signal_date=key[0],
                    layer=classify_daily_candidate(
                        price=price,
                        sma20=sma20,
                        sma60=sma60,
                        technical_score=score,
                    ),
                    price=price,
                    amount=amount,
                    technical_score=score,
                    sma20=sma20,
                    sma60=sma60,
                )
            )
        return output


__all__ = [
    "HistoricalCandidateLoader",
    "HistoricalScanCandidate",
    "eligible_signal_dates",
    "month_end_signal_dates",
]