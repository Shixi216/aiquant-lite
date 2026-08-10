"""Two-year A/B formal replay using the frozen overheat shadow score."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any

from scripts.run_formal_timeline_replay import _trade_counts
from scripts.run_market_environment_attribution import _curve
from scripts.run_timeline_quality_validation import (
    _bars_for_sample, _industry_code, _load_market_data,
)
from trading.experiments.ab_shadow_replay import (
    merge_decisions, replay_with_fixed_technical_scores,
)
from trading.experiments.formal_replay_loader import FormalReplayInputLoader, load_candidate_file
from trading.experiments.formal_strategy_validation import FormalStrategyReplayService
from trading.experiments.formal_timeline_replay import (
    BUY_ACTIONS, FormalTimelineReplayService,
)
from trading.experiments.overheat_penalty_shadow import (
    PenaltyCalibration, PenaltyComponent, shadow_score,
)
from trading.experiments.timeline_quality_validation import (
    TimelineConstraintValidationService, TimelineTradeSample, summarize_results,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
CANDIDATE_PATH = REPORT_DIR / "two-year-daily-candidates.json"
COVERAGE_PATH = REPORT_DIR / "two-year-announcement-coverage.json"
CALIBRATION_PATH = REPORT_DIR / "overheat-penalty-shadow-validation.json"
REPORT_PATH = REPORT_DIR / "overheat-ab-shadow-replay.json"
START = date(2024, 8, 1)
END = date(2026, 8, 6)
HORIZONS = (5, 10, 20)


def _moving_averages(actual, symbol: str, day: date) -> tuple[float, float] | None:
    dates = sorted(value for value in actual[symbol] if value <= day)
    if len(dates) < 60:
        return None
    closes = [float(actual[symbol][value].close) for value in dates[-60:]]
    return sum(closes[-20:]) / 20, sum(closes) / 60


def _excursions(outcomes: list[Any], packet_ids: set[str]) -> dict[str, Any]:
    selected = [item for item in outcomes if item.packet.packet_id in packet_ids
                and item.entered_preferred_zone]
    favorable = [float(item.maximum_favorable_excursion) for item in selected
                 if item.maximum_favorable_excursion is not None]
    adverse = [float(item.maximum_adverse_excursion) for item in selected
               if item.maximum_adverse_excursion is not None]
    return {
        "mfe_mean": None if not favorable else fmean(favorable),
        "mfe_median": None if not favorable else median(favorable),
        "mae_mean": None if not adverse else fmean(adverse),
        "mae_median": None if not adverse else median(adverse),
    }


def _build_group(
    name: str, decisions: list[Any], inputs: list[Any],
    actual, benchmarks, market_dates, memberships,
) -> dict[str, Any]:
    timeline = FormalTimelineReplayService().replay(decisions, inputs)
    outcomes = list(timeline.outcomes)
    preferred = [item for item in outcomes if item.entered_preferred_zone]
    decision_by_key = {(item.symbol, item.signal_date): item for item in decisions}
    symbol_bars = {
        symbol: _bars_for_sample(actual, market_dates, symbol)
        for symbol in {item.packet.symbol for item in outcomes}
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
    key_by_packet = {
        item.packet.packet_id: (item.packet.symbol, item.packet.signal_date)
        for item in outcomes
    }
    score_by_packet = {
        packet_id: decision_by_key[key].technical_score
        for packet_id, key in key_by_packet.items()
    }
    weights = {
        packet_id: (
            0.0 if decision_by_key[key].recommended_batches <= 0 else
            decision_by_key[key].target_position_ratio
            / decision_by_key[key].recommended_batches
        ) for packet_id, key in key_by_packet.items()
    }
    return {
        "name": name, "decisions": decisions, "timeline": timeline,
        "outcomes": outcomes, "details": details,
        "key_by_packet": key_by_packet, "score_by_packet": score_by_packet,
        "weights": weights,
    }


def _scope_metrics(
    group: dict[str, Any], packet_ids: set[str],
    *, actual, market_dates, winner_keys: set[tuple], loser_keys: set[tuple],
    overheated_keys: set[tuple],
) -> dict[str, Any]:
    outcomes = [item for item in group["outcomes"] if item.packet.packet_id in packet_ids]
    result = {
        "formal_packets": len(outcomes),
        "buy_packets": sum(item.packet.action in BUY_ACTIONS for item in outcomes),
        "strong_buy_packets": sum(item.packet.action == "STRONG_BUY" for item in outcomes),
        "entered_preferred_zone": sum(item.entered_preferred_zone for item in outcomes),
        "returns": {},
        **_excursions(group["outcomes"], packet_ids),
    }
    executed_20_keys = set()
    overheated_errors = 0
    for horizon in HORIZONS:
        rows = [item for item in group["details"][str(horizon)]
                if item["packet_id"] in packet_ids]
        objects = [item for item in rows]
        from trading.experiments.timeline_quality_validation import TimelineTradeResult
        typed = [TimelineTradeResult(**item) for item in objects]
        summary = summarize_results(typed, constrained=True)
        summary["true_portfolio"] = _curve(
            group["details"], packet_ids, group["weights"], actual, market_dates
        )[str(horizon)] if False else None
        # _curve consumes one horizon at a time through a compatible mapping below.
        result["returns"][str(horizon)] = summary
        if horizon == 20:
            for item in typed:
                if item.executed and item.net_return is not None:
                    key = group["key_by_packet"][item.packet_id]
                    executed_20_keys.add(key)
                    if key in overheated_keys and float(item.net_return) <= 0:
                        overheated_errors += 1
    for horizon in HORIZONS:
        rows_by_horizon = {str(horizon): [
            item for item in group["details"][str(horizon)]
            if item["packet_id"] in packet_ids
        ]}
        curve = _curve(
            rows_by_horizon[str(horizon)], group["weights"], actual, market_dates
        )
        result["returns"][str(horizon)]["true_portfolio"] = curve
    result.update({
        "top_10pct_winner_retention": (
            None if not winner_keys else len(executed_20_keys & winner_keys) / len(winner_keys)
        ),
        "bottom_10pct_loser_avoidance": (
            None if not loser_keys else 1 - len(executed_20_keys & loser_keys) / len(loser_keys)
        ),
        "high_score_overheated_errors": overheated_errors,
    })
    return result


def _calibration() -> PenaltyCalibration:
    raw = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))["calibration"]
    return PenaltyCalibration(
        expansion=PenaltyComponent(**raw["expansion"]),
        ma60_slope=PenaltyComponent(**raw["ma60_slope"]),
        maximum_component_multiplier=float(raw["maximum_component_multiplier"]),
    )


def main() -> int:
    trade_before = _trade_counts()
    calibration = _calibration()
    candidates = load_candidate_file(CANDIDATE_PATH)
    coverage = json.loads(COVERAGE_PATH.read_text(encoding="utf-8"))
    covered = set(coverage["successful_symbols"])
    candidates = [item for item in candidates if item.symbol in covered]
    inputs, replay_coverage = FormalReplayInputLoader().load(
        candidates, risk_event_covered_symbols=covered,
    )
    inputs = [item for item in inputs if item.financial_records]
    symbols = sorted({item.symbol for item in inputs})
    actual, _, benchmarks, market_dates, memberships, _ = _load_market_data(
        symbols, START, END,
    )
    market_index = {day: index for index, day in enumerate(market_dates)}

    service = FormalStrategyReplayService()
    a_decisions = service.replay_many(inputs)
    a_by_key = {(item.symbol, item.signal_date): item for item in a_decisions}
    features = {}
    shadow_scores = {}
    affected_inputs = []
    affected_scores = []
    neutral_slope_missing_inputs = 0
    for value in inputs:
        key = (value.symbol, value.signal_date)
        index = market_index[value.signal_date]
        current = _moving_averages(actual, value.symbol, value.signal_date)
        previous = _moving_averages(actual, value.symbol, market_dates[index - 5])
        if current is None:
            closes = [float(point.bar.close) for point in value.bars]
            ma20, ma60 = sum(closes[-20:]) / 20, sum(closes[-60:]) / 60
        else:
            ma20, ma60 = current
        if previous is None:
            neutral_slope_missing_inputs += 1
            ma60_slope = calibration.ma60_slope.median
        else:
            _, old_ma60 = previous
            ma60_slope = ma60 / old_ma60 - 1
        row = {
            "technical_score": a_by_key[key].technical_score,
            "ma20_ma60_expansion": ma20 / ma60 - 1,
            "ma60_slope_5": ma60_slope,
        }
        scored = shadow_score(row, calibration)
        features[key] = row
        shadow_scores[key] = scored
        if scored["total_penalty"] > 0:
            affected_inputs.append(value)
            affected_scores.append(scored["shadow_technical_score"])
    replacements = replay_with_fixed_technical_scores(
        service, affected_inputs, affected_scores,
    )
    b_decisions = merge_decisions(a_decisions, replacements)
    b_by_key = {(item.symbol, item.signal_date): item for item in b_decisions}
    invariant_mismatches = sum(
        a_by_key[key].fundamental_score != b_by_key[key].fundamental_score
        or a_by_key[key].veto_triggered != b_by_key[key].veto_triggered
        or a_by_key[key].market_regime != b_by_key[key].market_regime
        for key in a_by_key
    )
    if invariant_mismatches:
        raise RuntimeError(f"A/B invariant mismatch: {invariant_mismatches}")

    groups = {
        "A": _build_group("A", a_decisions, inputs, actual, benchmarks, market_dates, memberships),
        "B": _build_group("B", b_decisions, inputs, actual, benchmarks, market_dates, memberships),
    }
    a_twenty = [item for item in groups["A"]["details"]["20"]
                if item["executed"] and item["net_return"] is not None]
    a_twenty.sort(key=lambda item: float(item["net_return"]))
    tail = max(1, round(len(a_twenty) * .10))
    winner_keys = {groups["A"]["key_by_packet"][item["packet_id"]]
                   for item in a_twenty[-tail:]}
    loser_keys = {groups["A"]["key_by_packet"][item["packet_id"]]
                  for item in a_twenty[:tail]}
    overheated_keys = {
        key for key, value in features.items()
        if value["ma20_ma60_expansion"] >= calibration.expansion.q75
        or value["ma60_slope_5"] >= calibration.ma60_slope.q75
    }

    report_groups = {}
    for name, group in groups.items():
        buy_ids = {item.packet.packet_id for item in group["outcomes"]
                   if item.packet.action in BUY_ACTIONS}
        ranked = sorted(buy_ids, key=lambda packet_id: group["score_by_packet"][packet_id], reverse=True)
        scopes = {"all_formal_buy_signals": set(ranked)}
        for fraction in (.10, .20, .30):
            count = max(1, round(len(ranked) * fraction))
            scopes[f"top_{round(fraction * 100)}pct"] = set(ranked[:count])
        group_report = {
            "decision_actions": dict(sorted(Counter(item.action for item in group["decisions"]).items())),
            "buy_decisions": sum(item.action in BUY_ACTIONS for item in group["decisions"]),
            "strong_buy_decisions": sum(item.action == "STRONG_BUY" for item in group["decisions"]),
            "timeline_packets": len(group["outcomes"]),
            "entered_preferred_zone": sum(item.entered_preferred_zone for item in group["outcomes"]),
            "scopes": {},
        }
        for scope, ids in scopes.items():
            overall = _scope_metrics(
                group, ids, actual=actual, market_dates=market_dates,
                winner_keys=winner_keys, loser_keys=loser_keys,
                overheated_keys=overheated_keys,
            )
            periods = {}
            for period, predicate in {
                "research": lambda day: day <= date(2025, 11, 18),
                "embargo": lambda day: date(2025, 11, 18) < day < date(2025, 12, 25),
                "validation": lambda day: day >= date(2025, 12, 25),
            }.items():
                subset = {packet_id for packet_id in ids
                          if predicate(group["key_by_packet"][packet_id][1])}
                periods[period] = _scope_metrics(
                    group, subset, actual=actual, market_dates=market_dates,
                    winner_keys=winner_keys, loser_keys=loser_keys,
                    overheated_keys=overheated_keys,
                )
            regimes = {}
            regime_by_packet = {item.packet.packet_id: item.packet.market_regime
                                for item in group["outcomes"]}
            for regime in ("RISING", "SIDEWAYS", "FALLING"):
                subset = {packet_id for packet_id in ids
                          if regime_by_packet[packet_id] == regime}
                regimes[regime] = _scope_metrics(
                    group, subset, actual=actual, market_dates=market_dates,
                    winner_keys=winner_keys, loser_keys=loser_keys,
                    overheated_keys=overheated_keys,
                )
            group_report["scopes"][scope] = {
                "overall": overall, "time_periods": periods,
                "market_regimes": regimes,
            }
        report_groups[name] = group_report

    trade_after = _trade_counts()
    if trade_after != trade_before:
        raise RuntimeError("A/B shadow replay changed trade state")
    report = {
        "report_name": "AIQUANT-LITE overheat penalty A/B shadow replay",
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "coverage": {
            "start": str(START), "end": str(END),
            "formal_inputs": len(inputs), "symbols": len(symbols),
            "penalty_affected_inputs": len(affected_inputs),
            "neutral_slope_missing_inputs": neutral_slope_missing_inputs,
            "loader": asdict(replay_coverage),
        },
        "calibration_reused_without_change": calibration.as_dict(),
        "expansion_speed_penalized": False,
        "groups": report_groups,
        "invariants": {
            "same_inputs": True, "same_fundamentals": True,
            "same_veto": True, "same_decision_engine": True,
            "same_position_sizer": True, "same_timeline_execution": True,
            "same_a_share_constraints": True,
            "invariant_mismatches": invariant_mismatches,
        },
        "production_parameters_modified": False,
        "orders_created": 0, "positions_changed": 0,
        "trade_state_before": trade_before, "trade_state_after": trade_after,
    }
    REPORT_PATH.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    print(json.dumps({
        "coverage": report["coverage"],
        "A": {k: report_groups["A"][k] for k in (
            "buy_decisions", "strong_buy_decisions", "entered_preferred_zone")},
        "B": {k: report_groups["B"][k] for k in (
            "buy_decisions", "strong_buy_decisions", "entered_preferred_zone")},
        "trade_state_unchanged": True,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
