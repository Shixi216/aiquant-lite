"""Historical daily-bars service.

Serves the most recent N deduplicated RAW bars and, on request, derives
forward-adjusted (QFQ) prices in memory with exactly the same formula as
``FormalHistoryDataService.materialize_forward_adjusted_bars``:

    base_factor = ARG_MAX(adj_factor, trade_date)   # anchor: latest trade date
    ratio       = adj_factor[t] / base_factor
    open/high/low/close *= ratio                    # volume/amount untouched

Read-only: nothing is persisted back to the database.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from data_hub.repositories.historical_bars import HistoricalBarsRepository
from data_hub.schemas.historical_bars import (
    HistoricalBarPoint,
    HistoricalBarsResponse,
)
from data_hub.services.full_market_common import normalize_symbol

MA_WINDOWS = (5, 10, 20, 60)
MA_WARMUP = max(MA_WINDOWS) - 1


def _moving_average(values: list[float | None], window: int) -> float | None:
    if len(values) < window:
        return None
    segment = values[-window:]
    if any(value is None for value in segment):
        return None
    return sum(float(value) for value in segment) / window


class HistoricalBarsService:
    """Compose RAW bars, QFQ derivation, change_pct and moving averages."""

    def __init__(self, repository: HistoricalBarsRepository | None = None) -> None:
        self._repository = repository or HistoricalBarsRepository()

    def latest(
        self,
        *,
        symbol: str,
        days: int,
        end_date: date | None = None,
        adjustment_type: str = "FORWARD_ADJUSTED",
    ) -> HistoricalBarsResponse:
        canonical = normalize_symbol(symbol)
        if canonical is None:
            raise ValueError(f"invalid A-share symbol: {symbol}")
        candidates = list(dict.fromkeys([canonical, canonical.split(".")[0]]))
        upper = end_date or date.today()

        rows = self._repository.fetch_raw_bars(
            symbols=candidates,
            end_date=upper,
            limit=days + MA_WARMUP,
        )
        matched = [row for row in rows if row[0] == canonical] or rows
        if not matched:
            raise KeyError(f"no historical bars for {symbol}")
        matched_symbol = matched[0][0]

        effective = adjustment_type
        fallback = False
        fallback_reason: str | None = None
        anchor_date: date | None = None

        ratios = [1.0] * len(matched)
        if adjustment_type == "FORWARD_ADJUSTED":
            factors = self._repository.fetch_adjustment_factors(
                symbols=[matched_symbol],
                end_date=upper,
            )
            window_dates = {row[1] for row in matched}
            window_factors = {
                trade_date: value
                for trade_date, value in factors.items()
                if trade_date in window_dates
            }
            if not window_factors:
                fallback = True
                fallback_reason = "missing_adjustment_factors"
            elif len(window_factors) != len(matched):
                fallback = True
                fallback_reason = "factor_window_incomplete"
            else:
                anchor_date = max(window_factors)
                base_factor = window_factors[anchor_date]
                ratios = [
                    window_factors[row[1]] / base_factor for row in matched
                ]
                effective = "FORWARD_ADJUSTED"
            if fallback:
                effective = "RAW"
                ratios = [1.0] * len(matched)

        prices = [
            None if row[5] is None else float(row[5]) * ratio
            for row, ratio in zip(matched, ratios)
        ]
        change_pct = self._change_pct_series(prices)
        bars = self._build_bars(matched, ratios, prices, change_pct)[-days:]
        stale = self._is_stale(matched[-1][1])

        return HistoricalBarsResponse(
            symbol=matched_symbol,
            requested_adjustment_type=adjustment_type,
            effective_adjustment_type=effective,
            adjustment_fallback=fallback,
            adjustment_fallback_reason=fallback_reason,
            factor_anchor_date=anchor_date,
            bar_count=len(bars),
            first_trade_date=bars[0].trade_date if bars else None,
            last_trade_date=bars[-1].trade_date if bars else None,
            data_cutoff=max((row[8] for row in matched), default=None),
            stale=stale,
            generated_at=datetime.now(timezone.utc),
            bars=bars,
        )

    @staticmethod
    def _change_pct_series(closes: list[float | None]) -> list[float | None]:
        series: list[float | None] = []
        for index, close in enumerate(closes):
            if index == 0 or close is None or closes[index - 1] is None:
                series.append(None)
                continue
            series.append((close / closes[index - 1] - 1.0) * 100.0)
        return series

    def _build_bars(
        self,
        rows: list[tuple],
        ratios: list[float],
        prices: list[float | None],
        change_pct: list[float | None],
    ) -> list[HistoricalBarPoint]:
        bars: list[HistoricalBarPoint] = []
        for index, row in enumerate(rows):
            (
                _symbol, trade_date, open_, high, low, _close_raw,
                volume, amount, _data_cutoff,
            ) = row
            ratio = ratios[index]
            close = prices[index]
            bars.append(
                HistoricalBarPoint(
                    trade_date=trade_date,
                    open=None if open_ is None else float(open_) * ratio,
                    high=None if high is None else float(high) * ratio,
                    low=None if low is None else float(low) * ratio,
                    close=close,
                    volume=None if volume is None else float(volume),
                    amount=None if amount is None else float(amount),
                    change_pct=change_pct[index],
                    ma5=_moving_average(prices[: index + 1], 5),
                    ma10=_moving_average(prices[: index + 1], 10),
                    ma20=_moving_average(prices[: index + 1], 20),
                    ma60=_moving_average(prices[: index + 1], 60),
                )
            )
        return bars

    @staticmethod
    def _is_stale(last_trade_date: date) -> bool:
        try:
            from data_hub.services.trading_calendar_service import (
                TradingCalendarService,
            )

            completed, _status = TradingCalendarService().latest_completed_trade_date()
            return bool(completed is not None and last_trade_date < completed)
        except Exception:
            return False


__all__ = ["HistoricalBarsService", "MA_WINDOWS", "MA_WARMUP"]
