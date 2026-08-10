"""Run the read-only daily formal decision timeline replay."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean
from typing import Any, Iterable

from database.db import get_connection
from trading.experiments.formal_replay_loader import FormalReplayInputLoader
from trading.experiments.formal_strategy_validation import FormalStrategyReplayService
from trading.experiments.formal_timeline_replay import (
    BUY_ACTIONS, FormalTimelineReplayService, TimelineOutcome,
    summarize_timeline_returns,
)
from trading.experiments.formal_validation_metrics import FormalValidationMetricsService
from trading.experiments.historical_candidate_loader import HistoricalCandidateLoader

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
REPORT_PATH = REPORT_DIR / "formal-timeline-validation-result.json"
DETAIL_PATH = REPORT_DIR / "formal-timeline-outcomes.json"
CANDIDATE_PATH = REPORT_DIR / "daily-candidates.json"
COVERAGE_PATH = REPORT_DIR / "daily-announcement-coverage.json"
OLD_REPORT_PATH = REPORT_DIR / "formal-validation-result.json"
TRADE_TABLES = (
    "backtest_positions", "manual_holdings", "manual_trade_ledger",
    "manual_trades", "trade_plans", "trading_orders",
)
REGIMES = ("RISING", "FALLING", "SIDEWAYS")
LAYERS = ("CORE", "NEAR", "CONTROL")


def _trade_counts() -> dict[str, int]:
    with get_connection(read_only=True) as connection:
        return {
            table: int(connection.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0])
            for table in TRADE_TABLES
        }


def _timeline_summary(items: Iterable[TimelineOutcome]) -> dict[str, Any]:
    outcomes = list(items)
    entered = [item for item in outcomes if item.entered_preferred_zone]
    mfe = [float(item.maximum_favorable_excursion) for item in entered
           if item.maximum_favorable_excursion is not None]
    mae = [float(item.maximum_adverse_excursion) for item in entered
           if item.maximum_adverse_excursion is not None]
    return {
        "packets": len(outcomes),
        "entered_allowed_zone": sum(x.entered_allowed_zone for x in outcomes),
        "entered_preferred_zone": len(entered),
        "average_preferred_entry_day": (
            None if not entered else fmean(
                int(x.preferred_entry_day) for x in entered
                if x.preferred_entry_day is not None
            )
        ),
        "core_structure_still_valid_at_entry": sum(
            x.core_structure_at_entry is True for x in entered
        ),
        "average_maximum_favorable_excursion": None if not mfe else fmean(mfe),
        "average_maximum_adverse_excursion": None if not mae else fmean(mae),
        "returns": summarize_timeline_returns(entered),
    }


def _old_comparison(new_summary: dict[str, Any]) -> dict[str, Any]:
    if not OLD_REPORT_PATH.exists():
        return {"available": False}
    old = json.loads(OLD_REPORT_PATH.read_text(encoding="utf-8"))
    old_metrics = old["overall"]["after_constraints"]
    result: dict[str, Any] = {
        "available": True,
        "old_sampling": old["validation_period"]["sampling"],
        "old_signal_dates": old["validation_period"]["signal_dates"],
        "new_signal_dates": 167,
        "horizons": {},
    }
    for horizon, current in new_summary["returns"].items():
        previous = old_metrics[str(horizon)]
        new_average = current["average_return"]
        old_average = previous["average_return"]
        result["horizons"][horizon] = {
            "old_samples": previous["sample_count"],
            "new_samples": current["sample_count"],
            "sample_difference": current["sample_count"] - previous["sample_count"],
            "old_average_return": old_average,
            "new_average_return": new_average,
            "average_return_difference": (
                None if old_average is None or new_average is None
                else new_average - old_average
            ),
        }
    return result


def main() -> int:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    trade_before = _trade_counts()
    candidates = HistoricalCandidateLoader().load_daily(
        start_date=date(2025, 8, 1), end_date=date(2026, 8, 6),
    )
    candidate_dates = sorted({item.signal_date for item in candidates})
    candidate_symbols = sorted({item.symbol for item in candidates})
    if len(candidate_dates) != 167:
        raise RuntimeError(f"expected 167 signal dates, got {len(candidate_dates)}")
    CANDIDATE_PATH.write_text(json.dumps(
        [asdict(item) for item in candidates], ensure_ascii=False,
        indent=2, default=str,
    ), encoding="utf-8")
    announcement = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    covered = set(announcement["successful_symbols"])
    uncovered = sorted(set(candidate_symbols) - covered)
    if uncovered:
        raise RuntimeError(f"announcement PIT coverage missing: {uncovered}")
    inputs, replay_coverage = FormalReplayInputLoader().load(
        candidates, risk_event_covered_symbols=covered,
    )
    decisions = FormalStrategyReplayService().replay_many(inputs)
    timeline = FormalTimelineReplayService().replay(decisions, inputs)
    trade_after = _trade_counts()
    if trade_after != trade_before:
        raise RuntimeError("formal timeline replay changed trade state")

    outcomes = list(timeline.outcomes)
    packets = list(timeline.packets)
    overall = _timeline_summary(outcomes)
    by_layer = {
        layer: _timeline_summary(
            item for item in outcomes if item.packet.layer == layer
        ) for layer in LAYERS
    }
    by_regime = {
        regime: _timeline_summary(
            item for item in outcomes if item.packet.market_regime == regime
        ) for regime in REGIMES
    }
    direct = FormalValidationMetricsService().candidate_outcomes_by_layer(decisions)
    direct_by_layer = {
        layer: {
            horizon: asdict(metric)
            for horizon, metric in direct.get(layer, {}).items()
        } for layer in LAYERS
    }
    decision_map = {(item.symbol, item.signal_date): item for item in decisions}
    frozen_match_count = sum(
        packet.frozen_entry_zone
            == tuple(decision_map[(packet.symbol, packet.signal_date)].frozen_entry_zone)
        and packet.frozen_preferred_zone
            == tuple(decision_map[(packet.symbol, packet.signal_date)].frozen_preferred_zone)
        and packet.frozen_stop_loss_price
            == decision_map[(packet.symbol, packet.signal_date)].frozen_stop_loss_price
        for packet in packets
    )
    report = {
        "report_name": "AIQUANT-LITE daily frozen DecisionPacket timeline replay",
        "generated_at": datetime.now().astimezone(),
        "research_only": True,
        "production_parameters_updated": False,
        "orders_or_positions_changed": False,
        "trade_state_before": trade_before,
        "trade_state_after": trade_after,
        "coverage": {
            "start": candidate_dates[0], "end": candidate_dates[-1],
            "eligible_signal_dates": len(candidate_dates),
            "completed_signal_dates": timeline.signal_dates_completed,
            "candidate_rows": len(candidates),
            "candidate_symbols": len(candidate_symbols),
            "formal_inputs": len(inputs), "formal_decisions": len(decisions),
            "announcement_covered_symbols": len(covered),
            "replay_loader": asdict(replay_coverage),
        },
        "decision_packets": {
            "total": len(packets),
            "buy_actions": sum(x.action in BUY_ACTIONS for x in packets),
            "actions": dict(sorted(Counter(x.action for x in packets).items())),
            "duplicates_skipped": timeline.duplicate_signals_skipped,
            "terminal_reasons": dict(sorted(Counter(
                x.terminal_reason for x in outcomes
            ).items())),
            "frozen_zone_match_count": frozen_match_count,
            "frozen_zone_mismatch_count": len(packets) - frozen_match_count,
            "content_hashes_present": sum(bool(x.content_sha256) for x in packets),
        },
        "overall": overall,
        "by_regime": by_regime,
        "by_layer_after_preferred_entry": by_layer,
        "by_layer_directly_after_signal": direct_by_layer,
        "comparison_to_old_13_sample_replay": _old_comparison(overall),
        "point_in_time": {
            "future_leakage_detected": False,
            "validation_completed_without_future_data_exception": True,
            "actionable_zone_valid_decisions": sum(
                x.point_in_time_valid for x in decisions
            ),
            "non_actionable_or_no_zone_decisions": sum(
                not x.point_in_time_valid for x in decisions
            ),
            "risk_event_coverage_complete": all(
                x.risk_event_coverage_complete for x in inputs
            ),
            "historical_universe_complete": all(
                x.historical_universe_complete for x in inputs
            ),
        },
        "invariants": {
            "entry_zone_recomputed_after_packet": False,
            "preferred_zone_recomputed_after_packet": False,
            "stop_loss_recomputed_after_packet": False,
            "daily_update_inputs": "close price only",
            "orders_created": timeline.orders_created,
            "positions_changed": timeline.positions_changed,
        },
    }
    REPORT_PATH.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    DETAIL_PATH.write_text(json.dumps(
        [asdict(item) for item in outcomes], ensure_ascii=False,
        indent=2, default=str,
    ), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())