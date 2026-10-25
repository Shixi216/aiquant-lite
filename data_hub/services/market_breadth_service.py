"""Market breadth service: server-side aggregate + trade-date derivation."""
from __future__ import annotations

from datetime import date, datetime, timezone

from data_hub.repositories.market_breadth import MarketBreadthRepository
from data_hub.schemas.market_breadth import ChangeBuckets, MarketBreadthResponse

MARKET_TRADE_DATE_DERIVATION = (
    "trading_calendar.latest_completed_trade_date("
    "data_available_date=snapshot_data_trade_date or bars_max_trade_date)"
)


class MarketBreadthService:
    """Compose the breadth response from the full-universe SQL aggregate."""

    def __init__(self, repository: MarketBreadthRepository | None = None) -> None:
        self._repository = repository or MarketBreadthRepository()

    def latest(
        self,
        *,
        maximum_age_seconds: int,
    ) -> MarketBreadthResponse:
        persisted = self._repository.latest_breadth()
        if persisted is None:
            raise KeyError("no market snapshot")
        run, aggregates, snapshot_trade_date, bars_max_trade_date = persisted
        (
            snapshot_id, data_cutoff, expected_universe_size,
            received_symbol_count, valid_symbol_count, coverage_ratio,
            completeness_status, snapshot_time,
        ) = run

        now = datetime.now(timezone.utc)
        data_age_seconds = max(
            0.0,
            (now - snapshot_time.astimezone(timezone.utc)).total_seconds(),
        )
        market_trade_date, trade_date_status = self._resolve_trade_date(
            data_cutoff=data_cutoff,
            snapshot_trade_date=snapshot_trade_date,
            bars_max_trade_date=bars_max_trade_date,
        )
        partial_universe = not (
            received_symbol_count == expected_universe_size
            and coverage_ratio == 1.0
            and completeness_status == "COMPLETE"
        )
        return MarketBreadthResponse(
            snapshot_id=snapshot_id,
            snapshot_time=snapshot_time,
            data_cutoff=data_cutoff,
            expected_universe_size=expected_universe_size,
            received_symbol_count=received_symbol_count,
            valid_symbol_count=valid_symbol_count,
            coverage_ratio=coverage_ratio,
            completeness_status=completeness_status,
            stale=data_age_seconds > maximum_age_seconds,
            data_age_seconds=data_age_seconds,
            advances=aggregates["advances"],
            declines=aggregates["declines"],
            flats=aggregates["flats"],
            limit_ups=None,
            limit_downs=None,
            total_amount=aggregates["total_amount"],
            median_change_pct=aggregates["median_change_pct"],
            change_buckets=ChangeBuckets(
                gt7=aggregates["gt7"],
                p5_7=aggregates["p5_7"],
                p2_5=aggregates["p2_5"],
                p0_2=aggregates["p0_2"],
                flat=aggregates["flats"],
                n0_2=aggregates["n0_2"],
                n2_5=aggregates["n2_5"],
                n5_7=aggregates["n5_7"],
                lt7=aggregates["lt7"],
            ),
            market_trade_date=market_trade_date,
            market_trade_date_status=trade_date_status,
            market_trade_date_derivation=MARKET_TRADE_DATE_DERIVATION,
            snapshot_data_trade_date=snapshot_trade_date,
            bars_max_trade_date=bars_max_trade_date,
            partial_universe=partial_universe,
        )

    @staticmethod
    def _resolve_trade_date(
        *,
        data_cutoff: datetime,
        snapshot_trade_date: date | None,
        bars_max_trade_date: date | None,
    ) -> tuple[date | None, str | None]:
        try:
            from data_hub.services.trading_calendar_service import (
                TradingCalendarService,
            )

            return TradingCalendarService().latest_completed_trade_date(
                data_cutoff=data_cutoff,
                data_available_date=snapshot_trade_date or bars_max_trade_date,
            )
        except Exception:
            return None, None


__all__ = ["MarketBreadthService", "MARKET_TRADE_DATE_DERIVATION"]
