"""Tushare tail-fill: only missing trading days, RAW bars + factors.

Never re-pulls full history. Results are cached in-process (TTL) and are
never written to any DuckDB. Provider calls are synchronous, so they run in
an executor (see project GIL/reentrancy lessons).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from webui.config import QUOTE_TAIL_TTL_SECONDS


@dataclass(frozen=True)
class TailBars:
    bars: list[dict] = field(default_factory=list)
    factors: dict[str, float] = field(default_factory=dict)


@dataclass
class _CacheEntry:
    value: TailBars
    expires_at: float


_CACHE: dict[tuple[str, str, str], _CacheEntry] = {}


def _cache_get(key: tuple[str, str, str]) -> TailBars | None:
    entry = _CACHE.get(key)
    if entry is None or entry.expires_at < time.monotonic():
        return None
    return entry.value


def _provider_bars_sync(symbol: str, start: str, end: str) -> TailBars:
    from config.settings import settings
    from data_hub.providers.tushare_provider import TushareProvider

    provider = TushareProvider(token=settings.tushare_token)
    records = provider.get_daily_bars(symbol, start, end, adjustment_type="RAW")
    bars: list[dict] = []
    for record in records:
        payload = record.payload if hasattr(record, "payload") else {}
        if not payload:
            payload = record.data if hasattr(record, "data") else {}
        trade_date = str(payload.get("trade_date", ""))
        if len(trade_date) != 8:
            continue
        bars.append(
            {
                "trade_date": (
                    f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
                ),
                "open": payload.get("open"),
                "high": payload.get("high"),
                "low": payload.get("low"),
                "close": payload.get("close"),
                # Match canonical/replay units: shares and CNY.
                "volume": None if payload.get("vol") is None else float(payload["vol"]) * 100,
                "amount": None if payload.get("amount") is None else float(payload["amount"]) * 1000,
                "adj_factor": None,
            }
        )
    factor_rows = provider.get_adjustment_factors(symbol, start, end)
    factors: dict[str, float] = {}
    for row in factor_rows:
        raw_date = str(row.get("trade_date", ""))
        if len(raw_date) != 8:
            continue
        iso = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        try:
            factors[iso] = float(row["adj_factor"])
        except (TypeError, ValueError, KeyError):
            continue
    return TailBars(bars=sorted(bars, key=lambda bar: bar["trade_date"]), factors=factors)


async def fetch_tail(
    symbol: str,
    *,
    start_date: str,
    end_date: str,
) -> TailBars | None:
    """Fetch missing-day RAW bars + factors; None on any provider failure."""
    key = (symbol, start_date, end_date)
    cached = _cache_get(key)
    if cached is not None:
        return cached
    try:
        loop = asyncio.get_running_loop()
        value = await loop.run_in_executor(
            None, _provider_bars_sync, symbol, start_date, end_date
        )
    except Exception:
        return None
    _CACHE[key] = _CacheEntry(value, time.monotonic() + QUOTE_TAIL_TTL_SECONDS)
    return value


def next_day_after(iso_date: str) -> str:
    day = date.fromisoformat(iso_date)
    return (day + timedelta(days=1)).isoformat()


def clear_cache() -> None:
    _CACHE.clear()


__all__ = ["TailBars", "fetch_tail", "next_day_after", "clear_cache"]
