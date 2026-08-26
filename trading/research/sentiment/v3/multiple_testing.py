from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MultipleTestingResult:
    test_id: str
    raw_p_value: float
    adjusted_p_value: float
    q_value: float
    rejected: bool


def benjamini_hochberg(
    p_values: list[tuple[str, float | None]],
    *,
    alpha: float = 0.05,
) -> list[MultipleTestingResult]:
    """Apply BH-FDR to the complete declared test family without dropping rows."""
    if not 0 < alpha < 1:
        raise ValueError("alpha must be between zero and one")
    normalized = [
        (test_id, 1.0 if value is None else min(1.0, max(0.0, float(value))))
        for test_id, value in p_values
    ]
    ordered = sorted(normalized, key=lambda item: (item[1], item[0]))
    total = len(ordered)
    adjusted: dict[str, float] = {}
    running = 1.0
    for offset in range(total - 1, -1, -1):
        test_id, raw_p_value = ordered[offset]
        rank = offset + 1
        running = min(running, raw_p_value * total / rank)
        adjusted[test_id] = min(1.0, running)
    return [
        MultipleTestingResult(
            test_id=test_id,
            raw_p_value=raw_p_value,
            adjusted_p_value=adjusted[test_id],
            q_value=adjusted[test_id],
            rejected=adjusted[test_id] <= alpha,
        )
        for test_id, raw_p_value in normalized
    ]


__all__ = ["MultipleTestingResult", "benjamini_hochberg"]
