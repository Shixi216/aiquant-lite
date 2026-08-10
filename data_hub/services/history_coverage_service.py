from __future__ import annotations

from datetime import date, datetime

from config.settings import Settings, settings
from database.db import get_connection
from data_hub.repositories.history import HistoryRepository
from data_hub.schemas.full_market import CoverageMetric
from data_hub.schemas.history import (
    AdjustmentType,
    HistoryCoverageResponse,
    HistoryEligibilityCounts,
)


def _metric(numerator: int, denominator: int) -> CoverageMetric:
    return CoverageMetric(
        numerator=numerator,
        denominator=denominator,
        ratio=numerator / denominator if denominator else 0.0,
    )


class HistoryCoverageService:
    def __init__(
        self,
        *,
        repository: HistoryRepository | None = None,
        app_settings: Settings = settings,
    ) -> None:
        self.repository = repository or HistoryRepository()
        self.settings = app_settings

    def generate(
        self,
        *,
        data_cutoff: datetime,
        adjustment_type: AdjustmentType = AdjustmentType.RAW,
        as_of_trade_date: date | None = None,
    ) -> HistoryCoverageResponse:
        warnings: list[str] = []
        latest_completed = self.repository.latest_completed_trade_date(
            data_cutoff
        )
        report_date = as_of_trade_date or latest_completed
        if report_date is not None and (
            latest_completed is None or report_date > latest_completed
        ):
            raise ValueError(
                "as_of_trade_date must be a completed audited trading date"
            )
        days = self.repository.trading_days(
            data_cutoff=data_cutoff,
            end_date=report_date,
            limit=60,
        )
        if (
            as_of_trade_date is not None
            and (not days or days[-1] != as_of_trade_date)
        ):
            raise ValueError(
                "as_of_trade_date must be an audited trading date"
            )
        if len(days) < 20:
            warnings.append(
                "audited trading calendar has fewer than 20 completed days"
            )
        elif len(days) < 60:
            warnings.append(
                "audited trading calendar has fewer than 60 completed days"
            )
        required_20 = set(days[-20:]) if len(days) >= 20 else set()
        required_60 = set(days[-60:]) if len(days) >= 60 else set()
        threshold_20 = min(required_20) if required_20 else None
        threshold_60 = min(required_60) if required_60 else None
        latest_trade_date = days[-1] if days else None

        with get_connection() as connection:
            active_rows = connection.execute(
                """
                WITH latest_version AS (
                    SELECT universe_version
                    FROM stock_universe_versions
                    WHERE data_cutoff <= ?
                    ORDER BY effective_at DESC
                    LIMIT 1
                )
                SELECT symbol, list_date
                FROM stock_universe
                WHERE universe_version = (
                    SELECT universe_version FROM latest_version
                )
                  AND listing_status = 'ACTIVE'
                """,
                [data_cutoff],
            ).fetchall()
            active = {str(row[0]): row[1] for row in active_rows}
            special = {
                str(row[0])
                for row in connection.execute(
                    """
                    WITH latest_snapshot AS (
                        SELECT snapshot_id
                        FROM market_snapshot_runs
                        WHERE snapshot_time <= ?
                        ORDER BY snapshot_time DESC
                        LIMIT 1
                    )
                    SELECT symbol
                    FROM market_snapshot_items
                    WHERE snapshot_id = (
                        SELECT snapshot_id FROM latest_snapshot
                    )
                      AND is_suspended = TRUE
                    """,
                    [data_cutoff],
                ).fetchall()
                if str(row[0]) in active
            }
            long_suspended = {
                str(row[0])
                for row in connection.execute(
                    """
                    WITH ranked AS (
                        SELECT
                            i.symbol,
                            i.is_suspended,
                            ROW_NUMBER() OVER (
                                PARTITION BY i.symbol
                                ORDER BY r.snapshot_time DESC
                            ) AS observation_rank
                        FROM market_snapshot_items i
                        JOIN market_snapshot_runs r USING (snapshot_id)
                        WHERE r.snapshot_time <= ?
                    )
                    SELECT symbol
                    FROM ranked
                    WHERE observation_rank <= ?
                    GROUP BY symbol
                    HAVING COUNT(*) >= ? AND BOOL_AND(is_suspended)
                    """,
                    [
                        data_cutoff,
                        self.settings.history_long_suspension_min_snapshots,
                        self.settings.history_long_suspension_min_snapshots,
                    ],
                ).fetchall()
                if str(row[0]) in active
            }
            failed_collection = {
                str(row[0])
                for row in connection.execute(
                    """
                    WITH latest_attempt AS (
                        SELECT
                            item.symbol,
                            item.status,
                            ROW_NUMBER() OVER (
                                PARTITION BY item.symbol
                                ORDER BY run.started_at DESC, item.item_index DESC
                            ) AS attempt_rank
                        FROM historical_backfill_items item
                        JOIN historical_backfill_runs run USING (run_id)
                        WHERE run.started_at <= ?
                    )
                    SELECT symbol
                    FROM latest_attempt
                    WHERE attempt_rank = 1 AND status = 'FAILED'
                    """,
                    [data_cutoff],
                ).fetchall()
                if str(row[0]) in active
            }
            bar_rows = connection.execute(
                """
                SELECT DISTINCT symbol, trade_date
                FROM canonical_historical_bars
                WHERE adjustment_type = ?
                  AND verification_status != 'CONFLICT'
                  AND data_cutoff <= ?
                  AND data_available_time <= ?
                  AND trade_date IN (
                      SELECT UNNEST(?::DATE[])
                  )
                """,
                [
                    adjustment_type.value,
                    data_cutoff,
                    data_cutoff,
                    days,
                ],
            ).fetchall() if days else []
            conflicts = connection.execute(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE verification_status = 'CONFLICT'
                    ),
                    COUNT(*) FILTER (
                        WHERE verification_status = 'SINGLE_SOURCE'
                    )
                FROM canonical_historical_bars
                WHERE adjustment_type = ? AND data_cutoff <= ?
                """,
                [adjustment_type.value, data_cutoff],
            ).fetchone()
            calendar_providers = [
                str(row[0])
                for row in connection.execute(
                    """
                    SELECT DISTINCT provider
                    FROM trading_calendar_days
                    WHERE data_cutoff <= ?
                      AND is_trading_day = TRUE
                      AND calendar_date IN (
                          SELECT UNNEST(?::DATE[])
                      )
                    ORDER BY provider
                    """,
                    [data_cutoff, days],
                ).fetchall()
            ] if days else []

        bars_by_symbol: dict[str, set[date]] = {}
        for symbol, trade_date in bar_rows:
            bars_by_symbol.setdefault(str(symbol), set()).add(trade_date)
        eligible_20 = {
            symbol
            for symbol, list_date in active.items()
            if threshold_20 is not None
            and list_date is not None
            and list_date <= threshold_20
            and symbol not in long_suspended
        }
        eligible_60 = {
            symbol
            for symbol, list_date in active.items()
            if threshold_60 is not None
            and list_date is not None
            and list_date <= threshold_60
            and symbol not in long_suspended
        }
        covered_20 = sum(
            required_20.issubset(bars_by_symbol.get(symbol, set()))
            for symbol in eligible_20
        )
        covered_60 = sum(
            required_60.issubset(bars_by_symbol.get(symbol, set()))
            for symbol in eligible_60
        )
        unknown_count = sum(
            list_date is None for list_date in active.values()
        )
        new_20_count = sum(
            threshold_20 is not None
            and list_date is not None
            and list_date > threshold_20
            for list_date in active.values()
        )
        new_60_count = sum(
            threshold_60 is not None
            and list_date is not None
            and list_date > threshold_60
            for list_date in active.values()
        )

        if special and not long_suspended:
            warnings.append(
                "latest snapshot has suspended symbols, but there are not "
                "enough consecutive snapshots to classify long suspension"
            )
        if unknown_count:
            warnings.append(
                "symbols with unknown list_date are excluded from eligible "
                "history denominators"
            )
        if failed_collection:
            warnings.append(
                "failed_collection_count reports latest failed collection "
                "attempts only; new listings and long suspensions are separate"
            )
        return HistoryCoverageResponse(
            data_cutoff=data_cutoff,
            analysis_data_cutoff=data_cutoff,
            as_of_trade_date=report_date,
            report_generated_at=datetime.now().astimezone(),
            adjustment_type=adjustment_type,
            eligibility=HistoryEligibilityCounts(
                active_universe_count=len(active),
                eligible_20d_count=len(eligible_20),
                eligible_60d_count=len(eligible_60),
                long_suspended_count=len(long_suspended),
                special_status_count=len(special),
                newly_listed_20d_count=new_20_count,
                newly_listed_60d_count=new_60_count,
                unknown_list_date_count=unknown_count,
                failed_collection_count=len(failed_collection),
            ),
            history_20d=_metric(covered_20, len(eligible_20)),
            history_60d=_metric(covered_60, len(eligible_60)),
            conflict_bar_count=int(conflicts[0]),
            single_source_bar_count=int(conflicts[1]),
            latest_completed_trade_date=latest_trade_date,
            required_trade_dates_20d=sorted(required_20),
            required_trade_dates_60d=sorted(required_60),
            calendar_providers=calendar_providers,
            warnings=warnings,
        )


__all__ = ["HistoryCoverageService"]
