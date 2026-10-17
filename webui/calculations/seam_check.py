"""Seam check: reject merged bar chains that hide an adjustment break.

The chain merges RAW+RAW and applies QFQ once afterwards, so the acceptance
test is continuity of the ADJUSTED series across the seam: the first tail
bar's adjusted open must sit inside the normal daily move of the previous
local adjusted close. A raw tail spliced onto adjusted history (or a
mis-scaled factor on one side) shows up as a violent adjusted jump and is
rejected. A legitimate ex-dividend day is NOT rejected: its factor change
is already absorbed by the single-anchor QFQ.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_MAX_SINGLE_DAY_PCT = 21.0  # covers 20%-limit boards with margin


@dataclass(frozen=True)
class SeamVerdict:
    ok: bool
    reason: str | None = None


def check_seam(
    *,
    local_last_close: float | None,
    tail_first_open: float | None,
    max_single_day_pct: float = DEFAULT_MAX_SINGLE_DAY_PCT,
) -> SeamVerdict:
    """Both inputs must be on the SAME (adjusted) price series."""
    if local_last_close in (None, 0.0) or tail_first_open is None:
        return SeamVerdict(ok=False, reason="missing_seam_prices")
    price_move_pct = abs(tail_first_open / local_last_close - 1.0) * 100.0
    if price_move_pct > max_single_day_pct:
        return SeamVerdict(
            ok=False,
            reason=f"adjusted_price_gap_{price_move_pct:.1f}pct",
        )
    return SeamVerdict(ok=True)


__all__ = ["SeamVerdict", "check_seam", "DEFAULT_MAX_SINGLE_DAY_PCT"]
