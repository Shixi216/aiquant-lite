from __future__ import annotations

import tracemalloc
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from time import perf_counter
from typing import Any

from database.db import get_connection
from trading.experiments.hashing import stable_hash, stable_id
from trading.experiments.models import (
    HISTORICAL_REPLAY_VERSION,
    ExperimentRiskFlag,
    SignalType,
)
from trading.experiments.schemas import (
    ExperimentObservation,
    HistoricalReplayRequest,
    HistoricalWindow,
)


CHINA_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class HistoricalReplayBatch:
    observations: list[ExperimentObservation]
    window: HistoricalWindow
    signal_date_count: int
    symbol_count: int
    database_query_count: int
    elapsed_ms: float
    peak_memory_bytes: int
    query_plan_hash: str
    risk_flags: list[ExperimentRiskFlag]


_REPLAY_SQL = """
WITH deduplicated AS (
    SELECT
        bar_id, symbol, trade_date, event_time, data_available_time,
        data_cutoff, open, high, low, close, volume, amount
    FROM canonical_historical_bars
    WHERE adjustment_type = 'RAW'
      AND verification_status <> 'CONFLICT'
      AND data_available_time <= CAST(trade_date AS TIMESTAMP)
          + INTERVAL '16 hours'
      AND event_time <= CAST(trade_date AS TIMESTAMP)
          + INTERVAL '16 hours'
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY symbol, trade_date
        ORDER BY generated_at DESC, bar_id
    ) = 1
),
windowed AS (
    SELECT
        *,
        COUNT(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS history_count_20,
        LAG(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
        ) AS previous_close,
        FIRST_VALUE(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS close_20_start,
        AVG(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS ma5,
        AVG(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 9 PRECEDING AND CURRENT ROW
        ) AS ma10,
        AVG(close) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS ma20,
        AVG(volume) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        ) AS volume_ma5,
        AVG(volume) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND CURRENT ROW
        ) AS volume_ma20,
        MAX(high) OVER (
            PARTITION BY symbol ORDER BY trade_date
            ROWS BETWEEN 19 PRECEDING AND 1 PRECEDING
        ) AS previous_high_20
    FROM deduplicated
),
eligible AS (
    SELECT
        *,
        close / previous_close - 1 AS close_return_1d,
        close / close_20_start - 1 AS momentum_20d,
        volume / NULLIF(volume_ma20, 0) AS volume_ratio_20d,
        close / NULLIF(previous_high_20, 0) - 1 AS breakout_20d
    FROM windowed
    WHERE trade_date BETWEEN $start_date AND $end_date
      AND history_count_20 = 20
      AND open > 0 AND high > 0 AND low > 0 AND close > 0
      AND volume >= 0 AND amount >= 0
      AND previous_close > 0
),
components AS (
    SELECT
        *,
        PERCENT_RANK() OVER (
            PARTITION BY trade_date ORDER BY close_return_1d
        ) AS change_rank,
        PERCENT_RANK() OVER (
            PARTITION BY trade_date ORDER BY amount
        ) AS amount_rank,
        PERCENT_RANK() OVER (
            PARTITION BY trade_date ORDER BY momentum_20d
        ) AS momentum_rank,
        PERCENT_RANK() OVER (
            PARTITION BY trade_date ORDER BY volume_ratio_20d
        ) AS volume_rank,
        PERCENT_RANK() OVER (
            PARTITION BY trade_date ORDER BY breakout_20d
        ) AS breakout_rank
    FROM eligible
),
scored AS (
    SELECT
        *,
        (
            change_rank * 0.20
            + amount_rank * 0.25
            + momentum_rank * 0.25
            + volume_rank * 0.15
            + breakout_rank * 0.15
        ) AS scanner_score,
        (
            momentum_rank * 0.50
            + change_rank * 0.20
            + volume_rank * 0.15
            + breakout_rank * 0.15
        ) AS technical_score
    FROM components
),
ranked AS (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY trade_date ORDER BY scanner_score DESC, symbol
        ) AS scanner_rank,
        ROW_NUMBER() OVER (
            PARTITION BY trade_date ORDER BY technical_score DESC, symbol
        ) AS technical_rank,
        ROW_NUMBER() OVER (
            PARTITION BY trade_date ORDER BY amount DESC, symbol
        ) AS amount_top_rank,
        ROW_NUMBER() OVER (
            PARTITION BY trade_date ORDER BY momentum_20d DESC, symbol
        ) AS momentum_top_rank,
        ROW_NUMBER() OVER (
            PARTITION BY trade_date
            ORDER BY hash(symbol || CAST($random_seed AS VARCHAR)), symbol
        ) AS random_rank
    FROM scored
),
groups AS (
    SELECT
        'SCANNER_ONLY' AS signal_type, scanner_rank AS rank,
        symbol, trade_date, scanner_score, technical_score,
        close_return_1d, momentum_20d, amount, volume_ratio_20d,
        breakout_20d, bar_id
    FROM ranked WHERE scanner_rank <= $top_k
    UNION ALL
    SELECT
        'TECHNICAL_ONLY', technical_rank, symbol, trade_date,
        technical_score, technical_score, close_return_1d,
        momentum_20d, amount, volume_ratio_20d, breakout_20d, bar_id
    FROM ranked WHERE technical_rank <= $top_k
    UNION ALL
    SELECT
        'AMOUNT_TOP20', amount_top_rank, symbol, trade_date,
        amount_rank, technical_score, close_return_1d,
        momentum_20d, amount, volume_ratio_20d, breakout_20d, bar_id
    FROM ranked WHERE amount_top_rank <= $top_k
    UNION ALL
    SELECT
        'MOMENTUM_20D_TOP20', momentum_top_rank, symbol, trade_date,
        momentum_rank, technical_score, close_return_1d,
        momentum_20d, amount, volume_ratio_20d, breakout_20d, bar_id
    FROM ranked WHERE momentum_top_rank <= $top_k
    UNION ALL
    SELECT
        'RANDOM_TOP20', random_rank, symbol, trade_date,
        CAST(NULL AS DOUBLE), technical_score, close_return_1d,
        momentum_20d, amount, volume_ratio_20d, breakout_20d, bar_id
    FROM ranked WHERE random_rank <= $top_k
)
SELECT * FROM groups
ORDER BY trade_date, signal_type, rank, symbol
"""


class HistoricalReplayService:
    """Batch replay over fields actually present in canonical RAW daily bars."""

    @staticmethod
    def inspect_window() -> HistoricalWindow:
        with get_connection() as connection:
            dates = [
                row[0]
                for row in connection.execute(
                    """
                    SELECT DISTINCT trade_date
                    FROM canonical_historical_bars
                    WHERE adjustment_type = 'RAW'
                      AND verification_status <> 'CONFLICT'
                    ORDER BY trade_date
                    """
                ).fetchall()
            ]
        if not dates:
            return HistoricalWindow(
                available_history_start=None,
                available_history_end=None,
                distinct_trade_dates=0,
                earliest_signal_trade_date=None,
                latest_signal_trade_date_1d=None,
                latest_signal_trade_date_3d=None,
                latest_signal_trade_date_5d=None,
                latest_signal_trade_date_20d=None,
                full_horizon_signal_date_count=0,
            )
        earliest = dates[19] if len(dates) >= 20 else None

        def latest(horizon: int) -> date | None:
            return dates[-(horizon + 1)] if len(dates) > horizon else None

        full_count = max(0, len(dates) - 20 - 19)
        return HistoricalWindow(
            available_history_start=dates[0],
            available_history_end=dates[-1],
            distinct_trade_dates=len(dates),
            earliest_signal_trade_date=earliest,
            latest_signal_trade_date_1d=latest(1),
            latest_signal_trade_date_3d=latest(3),
            latest_signal_trade_date_5d=latest(5),
            latest_signal_trade_date_20d=latest(20),
            full_horizon_signal_date_count=full_count,
        )

    def replay(
        self,
        request: HistoricalReplayRequest,
        *,
        run_id: str,
        query_plan_hash: str,
    ) -> HistoricalReplayBatch:
        tracemalloc.start()
        started = perf_counter()
        window = self.inspect_window()
        if (
            window.earliest_signal_trade_date is None
            or window.latest_signal_trade_date_1d is None
        ):
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            return HistoricalReplayBatch(
                observations=[],
                window=window,
                signal_date_count=0,
                symbol_count=0,
                database_query_count=1,
                elapsed_ms=(perf_counter() - started) * 1000,
                peak_memory_bytes=peak,
                query_plan_hash=query_plan_hash,
                risk_flags=[
                    ExperimentRiskFlag.INSUFFICIENT_HISTORY,
                    ExperimentRiskFlag.RESEARCH_ONLY,
                    ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
                ],
            )
        start_date = max(
            request.start_trade_date or window.earliest_signal_trade_date,
            window.earliest_signal_trade_date,
        )
        end_date = min(
            request.end_trade_date or window.latest_signal_trade_date_1d,
            window.latest_signal_trade_date_1d,
        )
        if start_date > end_date:
            raise ValueError("requested replay window has no eligible signal date")
        with get_connection() as connection:
            rows = connection.execute(
                _REPLAY_SQL,
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "top_k": request.top_k,
                    "random_seed": request.random_seed,
                },
            ).fetchall()
        risk_flags = [
            ExperimentRiskFlag.PARTIAL_REPLAY,
            ExperimentRiskFlag.HISTORICAL_FEATURE_UNAVAILABLE,
            ExperimentRiskFlag.SURVIVORSHIP_BIAS_RISK,
            ExperimentRiskFlag.CORPORATE_ACTION_RISK,
            ExperimentRiskFlag.RESEARCH_ONLY,
            ExperimentRiskFlag.NOT_PROOF_OF_PROFITABILITY,
            ExperimentRiskFlag.MULTIPLE_TESTING_RISK,
        ]
        observations: list[ExperimentObservation] = []
        for (
            signal_type,
            rank,
            symbol,
            signal_date,
            scanner_score,
            technical_score,
            close_return_1d,
            momentum_20d,
            amount,
            volume_ratio_20d,
            breakout_20d,
            bar_id,
        ) in rows:
            generated_at = datetime.combine(
                signal_date,
                time(16, 0),
                tzinfo=CHINA_TZ,
            )
            features: dict[str, Any] = {
                "bar_id": bar_id,
                "close_return_1d": close_return_1d,
                "momentum_20d": momentum_20d,
                "amount": amount,
                "volume_ratio_20d": volume_ratio_20d,
                "breakout_20d": breakout_20d,
                "replay_version": HISTORICAL_REPLAY_VERSION,
            }
            signal_id = stable_id(
                "sig",
                {
                    "run_id": run_id,
                    "signal_type": signal_type,
                    "trade_date": signal_date,
                    "symbol": symbol,
                },
            )
            observations.append(
                ExperimentObservation(
                    observation_id=stable_id(
                        "obs",
                        {
                            "run_id": run_id,
                            "signal_id": signal_id,
                            "symbol": symbol,
                        },
                    ),
                    run_id=run_id,
                    signal_id=signal_id,
                    signal_type=SignalType(signal_type),
                    signal_trade_date=signal_date,
                    signal_generated_at=generated_at,
                    data_cutoff=generated_at,
                    actionable_from_trade_date=None,
                    symbol=symbol,
                    rank=int(rank),
                    scanner_score=(
                        None
                        if scanner_score is None
                        else float(scanner_score)
                    ),
                    technical_score=(
                        None
                        if technical_score is None
                        else float(technical_score)
                    ),
                    factor_coverage="1/5",
                    available_factors=["TECHNICAL"],
                    missing_factors=[
                        "FUNDAMENTAL",
                        "SENTIMENT",
                        "POLICY_NEWS",
                        "CAPITAL_FLOW",
                    ],
                    anomaly_types=["PRICE_VOLUME_HISTORY_REPLAY"],
                    risk_flags=risk_flags,
                    veto_status="NOT_EVALUATED",
                    query_plan_hash=query_plan_hash,
                    signal_snapshot_hash=stable_hash(features),
                    stale_snapshot=False,
                    point_in_time_valid=True,
                    is_suspended=False,
                    data_complete=True,
                    created_at=generated_at,
                )
            )
        elapsed_ms = (perf_counter() - started) * 1000
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return HistoricalReplayBatch(
            observations=observations,
            window=window,
            signal_date_count=len(
                {item.signal_trade_date for item in observations}
            ),
            symbol_count=len({item.symbol for item in observations}),
            database_query_count=2,
            elapsed_ms=elapsed_ms,
            peak_memory_bytes=peak,
            query_plan_hash=query_plan_hash,
            risk_flags=risk_flags,
        )


__all__ = ["HistoricalReplayBatch", "HistoricalReplayService"]
