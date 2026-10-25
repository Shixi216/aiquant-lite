from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Phase3Gate:
    eligible: bool
    reasons: tuple[str, ...]


def evaluate_phase3_gate(
    stats: dict[str, Any],
    *,
    full_trading_days_required: int = 3,
    full_test_failures: int,
    full_test_skipped: int,
) -> Phase3Gate:
    reasons: list[str] = []
    if int(stats.get("total", 0)) == 0:
        reasons.append("INSUFFICIENT_REAL_SAMPLE")
    if float(stats.get("canonical_success_rate", 0.0)) < 0.995:
        reasons.append("CANONICAL_SUCCESS_RATE_BELOW_99_5_PERCENT")
    if int(stats.get("unknown_divergence", 0)) != 0:
        reasons.append("UNKNOWN_DIVERGENCE_PRESENT")
    if int(stats.get("pit_diverged", 0)) != 0:
        reasons.append("PIT_REQUIRED_INCOMPLETE")
    if int(stats.get("veto_input_incomplete", 0)) != 0:
        reasons.append("VETO_INPUT_INCOMPLETE_REQUIRES_REVIEW")
    if int(stats.get("score_source_mismatch", 0)) != 0:
        reasons.append("SCORE_MISMATCH_REQUIRES_CASE_REVIEW")
    if int(stats.get("legacy_failures", 0)) != 0:
        reasons.append("LEGACY_FAILURE_PRESENT")
    if int(stats.get("trading_days", 0)) < full_trading_days_required:
        reasons.append("INSUFFICIENT_COMPLETE_TRADING_DAYS")
    if full_test_failures != 0:
        reasons.append("FULL_TEST_FAILURES_PRESENT")
    if full_test_skipped != 0:
        reasons.append("FULL_TEST_SKIPS_PRESENT")
    return Phase3Gate(eligible=not reasons, reasons=tuple(reasons))


__all__ = ["Phase3Gate", "evaluate_phase3_gate"]
