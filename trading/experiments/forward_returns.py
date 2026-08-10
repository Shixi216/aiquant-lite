from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from database.db import get_connection
from trading.experiments.hashing import stable_id
from trading.experiments.models import (
    FORWARD_LABEL_VERSION,
    SUPPORTED_HORIZONS,
    ExperimentRiskFlag,
    LabelStatus,
)
from trading.experiments.schemas import (
    ExperimentObservation,
    ForwardReturnLabel,
)


class ForwardReturnLabelService:
    """Create RAW next-open labels without changing stored signal snapshots."""

    @staticmethod
    def pending_labels(
        observations: list[ExperimentObservation],
        *,
        generated_at: datetime,
    ) -> list[ForwardReturnLabel]:
        return [
            ForwardReturnLabel(
                label_id=stable_id(
                    "lbl",
                    {
                        "observation_id": observation.observation_id,
                        "horizon": horizon,
                        "version": FORWARD_LABEL_VERSION,
                    },
                ),
                observation_id=observation.observation_id,
                symbol=observation.symbol,
                signal_trade_date=observation.signal_trade_date,
                horizon_trading_days=horizon,
                label_status=(
                    LabelStatus.PENDING
                    if observation.point_in_time_valid
                    else LabelStatus.INVALID_POINT_IN_TIME
                ),
                risk_flags=(
                    []
                    if observation.point_in_time_valid
                    else [ExperimentRiskFlag.POINT_IN_TIME_INVALID]
                ),
                generated_at=generated_at,
            )
            for observation in observations
            for horizon in SUPPORTED_HORIZONS
        ]

    @staticmethod
    def _calendar(as_of: datetime) -> list[date]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT calendar_date
                FROM trading_calendar_days
                WHERE is_trading_day
                  AND calendar_date <= CAST(? AS DATE)
                ORDER BY calendar_date
                """,
                [as_of],
            ).fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _bars(
        symbols: list[str],
        *,
        as_of: datetime,
    ) -> dict[str, dict[date, dict[str, float | None]]]:
        if not symbols:
            return {}
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT symbol, trade_date, open, high, low, close
                FROM canonical_historical_bars
                WHERE symbol IN (SELECT UNNEST(?::VARCHAR[]))
                  AND adjustment_type = 'RAW'
                  AND verification_status <> 'CONFLICT'
                  AND data_available_time <= ?
                  AND data_cutoff <= ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY symbol, trade_date
                    ORDER BY generated_at DESC, bar_id
                ) = 1
                ORDER BY symbol, trade_date
                """,
                [symbols, as_of, as_of],
            ).fetchall()
        result: dict[str, dict[date, dict[str, float | None]]] = defaultdict(dict)
        for symbol, trade_date, open_, high, low, close in rows:
            result[symbol][trade_date] = {
                "open": None if open_ is None else float(open_),
                "high": None if high is None else float(high),
                "low": None if low is None else float(low),
                "close": None if close is None else float(close),
            }
        return dict(result)

    @staticmethod
    def _benchmark_returns(
        signal_dates: list[date],
        *,
        as_of: datetime,
    ) -> dict[tuple[date, int], float]:
        if not signal_dates:
            return {}
        output: dict[tuple[date, int], float] = {}
        with get_connection() as connection:
            for horizon in SUPPORTED_HORIZONS:
                rows = connection.execute(
                    """
                    WITH calendar AS (
                        SELECT
                            calendar_date,
                            ROW_NUMBER() OVER (ORDER BY calendar_date) AS rn
                        FROM trading_calendar_days
                        WHERE is_trading_day
                          AND calendar_date <= CAST($as_of AS DATE)
                    ),
                    requested AS (
                        SELECT calendar_date AS signal_trade_date, rn
                        FROM calendar
                        WHERE calendar_date IN (
                            SELECT UNNEST($signal_dates::DATE[])
                        )
                    ),
                    dates AS (
                        SELECT
                            requested.signal_trade_date,
                            entry.calendar_date AS entry_trade_date,
                            exit.calendar_date AS exit_trade_date
                        FROM requested
                        JOIN calendar AS entry
                          ON entry.rn = requested.rn + 1
                        JOIN calendar AS exit
                          ON exit.rn = requested.rn + $horizon
                    ),
                    bars AS (
                        SELECT symbol, trade_date, open, close
                        FROM canonical_historical_bars
                        WHERE adjustment_type = 'RAW'
                          AND verification_status <> 'CONFLICT'
                          AND data_available_time <= $as_of
                          AND data_cutoff <= $as_of
                        QUALIFY ROW_NUMBER() OVER (
                            PARTITION BY symbol, trade_date
                            ORDER BY generated_at DESC, bar_id
                        ) = 1
                    ),
                    signal_history AS (
                        SELECT
                            *,
                            COUNT(close) OVER (
                                PARTITION BY symbol ORDER BY trade_date
                                ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
                            ) AS history_count_20
                        FROM bars
                    ),
                    eligible AS (
                        SELECT dates.signal_trade_date, signal.symbol,
                               entry.open AS entry_price,
                               exit.close AS exit_price
                        FROM dates
                        JOIN signal_history AS signal
                          ON signal.trade_date = dates.signal_trade_date
                        JOIN bars AS entry
                          ON entry.symbol = signal.symbol
                         AND entry.trade_date = dates.entry_trade_date
                        JOIN bars AS exit
                          ON exit.symbol = signal.symbol
                         AND exit.trade_date = dates.exit_trade_date
                        WHERE signal.history_count_20 = 20
                          AND entry.open > 0 AND exit.close > 0
                    )
                    SELECT
                        signal_trade_date,
                        AVG(exit_price / entry_price - 1) AS benchmark_return
                    FROM eligible
                    GROUP BY signal_trade_date
                    """,
                    {
                        "as_of": as_of,
                        "signal_dates": signal_dates,
                        "horizon": horizon,
                    },
                ).fetchall()
                for signal_date, value in rows:
                    output[(signal_date, horizon)] = float(value)
        return output

    def calculate(
        self,
        observations: list[ExperimentObservation],
        *,
        as_of: datetime,
    ) -> list[ForwardReturnLabel]:
        if not observations:
            return []
        calendar = self._calendar(as_of)
        calendar_index = {value: index for index, value in enumerate(calendar)}
        bars = self._bars(
            sorted({item.symbol for item in observations}),
            as_of=as_of,
        )
        benchmarks = self._benchmark_returns(
            sorted({item.signal_trade_date for item in observations}),
            as_of=as_of,
        )
        labels: list[ForwardReturnLabel] = []
        for observation in observations:
            for horizon in SUPPORTED_HORIZONS:
                labels.append(
                    self._calculate_one(
                        observation,
                        horizon=horizon,
                        as_of=as_of,
                        calendar=calendar,
                        calendar_index=calendar_index,
                        symbol_bars=bars.get(observation.symbol, {}),
                        benchmark_return=benchmarks.get(
                            (observation.signal_trade_date, horizon)
                        ),
                    )
                )
        return labels

    @staticmethod
    def _calculate_one(
        observation: ExperimentObservation,
        *,
        horizon: int,
        as_of: datetime,
        calendar: list[date],
        calendar_index: dict[date, int],
        symbol_bars: dict[date, dict[str, float | None]],
        benchmark_return: float | None,
    ) -> ForwardReturnLabel:
        identity = {
            "observation_id": observation.observation_id,
            "horizon": horizon,
            "version": FORWARD_LABEL_VERSION,
        }
        common: dict[str, Any] = {
            "label_id": stable_id("lbl", identity),
            "observation_id": observation.observation_id,
            "symbol": observation.symbol,
            "signal_trade_date": observation.signal_trade_date,
            "horizon_trading_days": horizon,
            "benchmark_return": benchmark_return,
            "calculated_at": as_of,
            "generated_at": observation.created_at,
        }
        if not observation.point_in_time_valid:
            return ForwardReturnLabel(
                **common,
                label_status=LabelStatus.INVALID_POINT_IN_TIME,
                missing_price_reason="signal was not point-in-time valid",
                risk_flags=[ExperimentRiskFlag.POINT_IN_TIME_INVALID],
            )
        index = calendar_index.get(observation.signal_trade_date)
        if index is None or index + horizon >= len(calendar):
            return ForwardReturnLabel(
                **common,
                label_status=LabelStatus.INSUFFICIENT_FUTURE_DATA,
                missing_price_reason="required future trading day is unavailable",
                risk_flags=[ExperimentRiskFlag.INSUFFICIENT_FUTURE_DATA],
            )
        entry_date = calendar[index + 1]
        exit_date = calendar[index + horizon]
        entry = symbol_bars.get(entry_date)
        if entry is None or entry["open"] is None or entry["open"] <= 0:
            return ForwardReturnLabel(
                **common,
                entry_trade_date=entry_date,
                exit_trade_date=exit_date,
                label_status=LabelStatus.UNTRADABLE,
                was_suspended_on_entry=True,
                missing_price_reason="next trading day open is unavailable",
                risk_flags=[
                    ExperimentRiskFlag.MISSING_ENTRY_PRICE,
                    ExperimentRiskFlag.UNTRADABLE_SIGNAL,
                ],
            )
        exit_bar = symbol_bars.get(exit_date)
        if (
            exit_bar is None
            or exit_bar["close"] is None
            or exit_bar["close"] <= 0
        ):
            return ForwardReturnLabel(
                **common,
                entry_trade_date=entry_date,
                exit_trade_date=exit_date,
                entry_price=entry["open"],
                label_status=LabelStatus.MISSING_EXIT,
                missing_price_reason="horizon trading day close is unavailable",
                risk_flags=[ExperimentRiskFlag.MISSING_EXIT_PRICE],
            )
        holding_dates = calendar[index + 1 : index + horizon + 1]
        holding_bars = [
            symbol_bars[value]
            for value in holding_dates
            if value in symbol_bars
        ]
        missing_during = len(holding_bars) != len(holding_dates)
        entry_price = float(entry["open"])
        exit_price = float(exit_bar["close"])
        highs = [
            float(value["high"])
            for value in holding_bars
            if value["high"] is not None
        ]
        lows = [
            float(value["low"])
            for value in holding_bars
            if value["low"] is not None
        ]
        gross_return = exit_price / entry_price - 1
        risk_flags = [ExperimentRiskFlag.CORPORATE_ACTION_RISK]
        if observation.stale_snapshot:
            risk_flags.append(ExperimentRiskFlag.STALE_SIGNAL)
        return ForwardReturnLabel(
            **common,
            entry_trade_date=entry_date,
            exit_trade_date=exit_date,
            entry_price=entry_price,
            exit_price=exit_price,
            gross_return=gross_return,
            excess_return=(
                None
                if benchmark_return is None
                else gross_return - benchmark_return
            ),
            maximum_favorable_excursion=(
                None if not highs else max(value / entry_price - 1 for value in highs)
            ),
            maximum_adverse_excursion=(
                None if not lows else min(value / entry_price - 1 for value in lows)
            ),
            was_suspended_on_entry=False,
            was_suspended_during_holding=missing_during,
            was_price_limit_locked=None,
            label_status=LabelStatus.COMPLETE,
            risk_flags=risk_flags,
        )


__all__ = ["ForwardReturnLabelService"]
