"""Pure moving-average helpers (server-side; the frontend never computes)."""
from __future__ import annotations

MA_WINDOWS = (5, 10, 20, 60)


def moving_average(values: list[float | None], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    segment = values[-window:]
    if any(value is None for value in segment):
        return None
    return sum(float(value) for value in segment) / window


def ma_series(values: list[float | None]) -> dict[str, list[float | None]]:
    return {
        f"ma{window}": [
            moving_average(values[: index + 1], window)
            for index in range(len(values))
        ]
        for window in MA_WINDOWS
    }


__all__ = ["MA_WINDOWS", "moving_average", "ma_series"]
