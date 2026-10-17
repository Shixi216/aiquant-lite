"""Pure per-bar metrics derived from an adjusted close series."""
from __future__ import annotations


def change_pct_series(closes: list[float | None]) -> list[float | None]:
    series: list[float | None] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else None
        if close is None or previous in (None, 0.0):
            series.append(None)
            continue
        series.append((close / float(previous) - 1.0) * 100.0)
    return series


def amplitude(high: float | None, low: float | None, previous: float | None) -> float | None:
    if high is None or low is None or previous in (None, 0.0):
        return None
    return (float(high) - float(low)) / float(previous) * 100.0


__all__ = ["change_pct_series", "amplitude"]
