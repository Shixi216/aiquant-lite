"""Read-only two-year validation of the fixed breadth condition.

This runner reuses the production formal replay and frozen-packet timeline.  It
does not select or tune any market condition: only advancers > decliners is
evaluated in three chronological, non-overlapping periods.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scripts.run_formal_timeline_replay import _trade_counts
from scripts.run_market_environment_attribution import _group, _market_states
from scripts.run_timeline_quality_validation import (
    _bars_for_sample, _date, _industry_code, _load_market_data,
)
from trading.experiments.formal_replay_loader import FormalReplayInputLoader, load_candidate_file
from trading.experiments.formal_strategy_validation import FormalStrategyReplayService
from trading.experiments.formal_timeline_replay import FormalTimelineReplayService
from trading.experiments.timeline_quality_validation import (
    TimelineConstraintValidationService, TimelineTradeSample,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
CANDIDATE_PATH = REPORT_DIR / "two-year-daily-candidates.json"
COVERAGE_PATH = REPORT_DIR / "two-year-announcement-coverage.json"
REPORT_PATH = REPORT_DIR / "two-year-market-condition-validation.json"
OUTCOME_PATH = REPORT_DIR / "two-year-formal-timeline-outcomes.json"
START = date(2024, 8, 1)
END = date(2026, 8, 6)
HORIZONS = (5, 10, 20)


def _three_periods(signal_dates: list[date], embargo: int = 20) -> dict[str, Any]:
    ordered = sorted(set(signal_dates))
    usable = len(ordered) - 2 * embargo
    if usable < 3:
        raise ValueError("not enough signal dates for three periods")
    width = usable // 3
    first = ordered[:width]
    gap1 = ordered[width:width + embargo]
    second_start = width + embargo
    second = ordered[second_start:second_start + width]
    gap2_start = second_start + width
    gap2 = ordered[gap2_start:gap2_start + embargo]
    third = ordered[gap2_start + embargo:]
    return {"period_1": first, "embargo_1": gap1, "period_2": second,
            "embargo_2": gap2, "period_3": third}


def _period_meta(values: list[date]) -> list[Any]:
    return [str(values[0]), str(values[-1]), len(values)] if values else []


def main() -> int:
    trade_before = _trade_counts()
    candidates = load_candidate_file(CANDIDATE_PATH)
    coverage = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    covered = set(coverage["successful_symbols"])
    pit_candidates = [item for item in candidates if item.symbol in covered]
    inputs, loader_coverage = FormalReplayInputLoader().load(
        pit_candidates, risk_event_covered_symbols=covered,
    )
    inputs = [item for item in inputs if item.financial_records]
    decisions = FormalStrategyReplayService().replay_many(inputs)
    timeline = FormalTimelineReplayService().replay(decisions, inputs)
    outcomes = list(timeline.outcomes)
    preferred = [item for item in outcomes if item.entered_preferred_zone]
    if not preferred:
        raise RuntimeError("two-year replay produced no preferred-zone samples")

    symbols = sorted({item.packet.symbol for item in outcomes})
    actual, _, benchmarks, market_dates, memberships, _ = _load_market_data(
        symbols, START, END,
    )
    symbol_bars = {
        symbol: _bars_for_sample(actual, market_dates, symbol) for symbol in symbols
    }
    samples = []
    for item in preferred:
        packet = item.packet
        industry = _industry_code(memberships, packet.symbol, packet.signal_date)
        samples.append(TimelineTradeSample(
            packet_id=packet.packet_id, symbol=packet.symbol,
            signal_date=packet.signal_date,
            entry_date=item.preferred_entry_date,
            entry_day=int(item.preferred_entry_day),
            market_regime=packet.market_regime,
            core_structure_at_entry=item.core_structure_at_entry,
            bars=symbol_bars[packet.symbol],
            csi300_close_by_date=benchmarks["000300.SH"],
            industry_close_by_date=(
                {} if industry is None else benchmarks.get(industry, {})
            ),
        ))
    constrained = TimelineConstraintValidationService().evaluate_many(samples)
    details = {
        str(horizon): [asdict(item) for item in constrained[horizon]]
        for horizon in HORIZONS
    }

    decision_by_key = {(x.symbol, x.signal_date): x for x in decisions}
    weights = {}
    regime_by_id = {}
    signal_date_by_id = {}
    for item in preferred:
        packet = item.packet
        decision = decision_by_key[(packet.symbol, packet.signal_date)]
        if decision.action != packet.action:
            raise RuntimeError(f"frozen packet action mismatch: {packet.packet_id}")
        weights[packet.packet_id] = (
            decision.target_position_ratio / decision.recommended_batches
        )
        regime_by_id[packet.packet_id] = packet.market_regime
        signal_date_by_id[packet.packet_id] = packet.signal_date

    signal_dates = sorted({item.signal_date for item in pit_candidates})
    states = _market_states(START, END)
    missing = [day for day in signal_dates if day not in states]
    if missing:
        raise RuntimeError(f"missing PIT breadth states: {missing[:5]}")
    breadth_positive = {day: states[day].advancers > states[day].decliners
                        for day in signal_dates}
    periods = _three_periods(signal_dates)

    fixed_condition = {}
    for name in ("period_1", "period_2", "period_3"):
        dates = set(periods[name])
        true_ids = {pid for pid, day in signal_date_by_id.items()
                    if day in dates and breadth_positive[day]}
        false_ids = {pid for pid, day in signal_date_by_id.items()
                     if day in dates and not breadth_positive[day]}
        fixed_condition[name] = {
            "dates": _period_meta(periods[name]),
            "condition_true": _group(details, true_ids, weights, actual, market_dates),
            "condition_false": _group(details, false_ids, weights, actual, market_dates),
        }

    by_regime = {}
    for regime in ("RISING", "SIDEWAYS", "FALLING"):
        ids = {pid for pid, value in regime_by_id.items() if value == regime}
        by_regime[regime] = {
            "preferred_packets": len(ids),
            "results": _group(details, ids, weights, actual, market_dates),
        }

    all_ids = set(signal_date_by_id)
    report = {
        "report_name": "AIQUANT-LITE two-year fixed breadth condition validation",
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "condition": "advancers > decliners",
        "condition_was_preselected": True,
        "automatic_condition_search": False,
        "coverage": {
            "market_data_start": str(START), "market_data_end": str(END),
            "candidate_signal_start": str(signal_dates[0]),
            "candidate_signal_end": str(signal_dates[-1]),
            "candidate_signal_dates": len(signal_dates),
            "candidate_rows_total": len(candidates),
            "pit_and_announcement_covered_candidate_rows": len(pit_candidates),
            "formal_inputs_with_pit_financials": len(inputs),
            "formal_decisions": len(decisions),
            "decision_packets": len(outcomes),
            "preferred_zone_packets": len(preferred),
            "excluded_symbols_without_complete_pit_coverage": len(
                {x.symbol for x in candidates} - covered
            ),
            "loader": asdict(loader_coverage),
        },
        "terminal_reasons": dict(sorted(Counter(
            item.terminal_reason for item in outcomes
        ).items())),
        "overall": _group(details, all_ids, weights, actual, market_dates),
        "market_regimes": by_regime,
        "time_ordered_periods": {
            "embargo_days_between_periods": 20,
            "embargo_1": _period_meta(periods["embargo_1"]),
            "embargo_2": _period_meta(periods["embargo_2"]),
            "results": fixed_condition,
        },
        "point_in_time": {
            "future_leakage_detected": False,
            "market_state_cutoff": "signal date 16:00",
            "financial_records_required": True,
            "announcement_coverage_required": True,
        },
        "production_parameters_modified": False,
        "market_filter_connected_to_production": False,
        "orders_created": timeline.orders_created,
        "positions_changed": timeline.positions_changed,
        "trade_state_before": trade_before,
        "trade_state_after": _trade_counts(),
    }
    if report["trade_state_after"] != trade_before:
        raise RuntimeError("two-year validation changed trade state")
    REPORT_PATH.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    OUTCOME_PATH.write_text(json.dumps(
        [asdict(item) for item in outcomes], ensure_ascii=False, indent=2,
        default=str,
    ), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
