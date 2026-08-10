"""Research-only A/B/C core-candidate ranking walk-forward validation."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime
from math import ceil
from pathlib import Path
from statistics import fmean, median
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_market_environment_attribution import _curve
from scripts.run_timeline_quality_validation import (
    _bars_for_sample,
    _date,
    _industry_code,
    _load_market_data,
    _trade_counts,
)
from trading.decision_support.action import Action
from trading.decision_support.data_status import DataStatus
from trading.decision_support.position_sizer import PositionSizer, SizerInput
from trading.experiments.core_ranking_walk_forward import (
    HORIZONS,
    RANKING_NAMES,
    TOP_FRACTIONS,
    assign_ranking_scores,
    build_walk_forward_folds,
    factor_information_coefficients,
    select_daily_top_fraction,
)
from trading.experiments.overheat_penalty_shadow import (
    PenaltyCalibration,
    PenaltyComponent,
    shadow_score,
)
from trading.experiments.timeline_quality_validation import (
    TimelineConstraintValidationService,
    TimelineTradeResult,
    TimelineTradeSample,
    distribution_diagnostics,
    summarize_results,
)


TZ = ZoneInfo("Asia/Shanghai")
SOURCE_DIR = ROOT / "reports" / "formal_history_validation"
RESEARCH_DIR = ROOT / "reports" / "research"
CANDIDATE_PATH = SOURCE_DIR / "two-year-daily-candidates.json"
OUTCOME_PATH = SOURCE_DIR / "two-year-formal-timeline-outcomes.json"
CALIBRATION_PATH = SOURCE_DIR / "overheat-penalty-shadow-validation.json"
REPORT_PATH = RESEARCH_DIR / "core-ranking-walk-forward.json"
START = date(2024, 8, 1)
END = date(2026, 8, 6)
BUY_ACTIONS = {"STRONG_BUY", "BUY", "SMALL_BUY"}
FACTOR_FIELDS = (
    "technical_score",
    "fundamental_score",
    "expansion_speed_5",
    "ma20_ma60_expansion",
    "ma60_slope_5",
    "signal_day_return",
    "prior_return_10",
    "price_distance_ma20",
)


def _calibration() -> PenaltyCalibration:
    payload = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    raw = payload["calibration"]
    return PenaltyCalibration(
        expansion=PenaltyComponent(**raw["expansion"]),
        ma60_slope=PenaltyComponent(**raw["ma60_slope"]),
        maximum_component_multiplier=float(raw["maximum_component_multiplier"]),
    )


def _adjusted_close(bar: Any) -> float:
    factor = 1.0 if bar.adjustment_factor is None else float(bar.adjustment_factor)
    return float(bar.close) * factor


def _features(
    *, symbol: str, signal_date: date, candidate: dict[str, Any], actual: Any,
) -> dict[str, float | None]:
    dates = sorted(day for day in actual[symbol] if day <= signal_date)
    if not dates or dates[-1] != signal_date:
        raise RuntimeError(f"missing signal bar for {symbol} {signal_date}")
    index = len(dates) - 1
    current = actual[symbol][signal_date]
    ma20 = float(candidate["sma20"])
    ma60 = float(candidate["sma60"])
    expansion = ma20 / ma60 - 1
    old_ma20 = old_ma60 = None
    if index >= 64:
        old_closes = [
            float(actual[symbol][day].close)
            for day in dates[index - 64:index - 4]
        ]
        if len(old_closes) == 60:
            old_ma20 = fmean(old_closes[-20:])
            old_ma60 = fmean(old_closes)
    signal_day_return = None
    if index >= 1:
        previous = actual[symbol][dates[index - 1]]
        if previous.close:
            signal_day_return = float(current.close) / float(previous.close) - 1
    prior_return_10 = None
    if index >= 10:
        old = actual[symbol][dates[index - 10]]
        old_close = _adjusted_close(old)
        if old_close > 0:
            prior_return_10 = _adjusted_close(current) / old_close - 1
    return {
        "ma20_ma60_expansion": expansion,
        "ma60_slope_5": (
            None if old_ma60 is None else ma60 / old_ma60 - 1
        ),
        "expansion_speed_5": (
            None
            if old_ma20 is None or old_ma60 is None
            else expansion - (old_ma20 / old_ma60 - 1)
        ),
        "signal_day_return": signal_day_return,
        "prior_return_10": prior_return_10,
        "price_distance_ma20": float(candidate["price"]) / ma20 - 1,
    }


def _ranking_rows(outcomes: list[dict[str, Any]], actual: Any) -> list[dict[str, Any]]:
    calibration = _calibration()
    candidates = {
        (item["symbol"], _date(item["signal_date"])): item
        for item in json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))
    }
    rows = []
    missing_slope = 0
    for item in outcomes:
        packet = item["packet"]
        if packet["layer"] != "CORE" or packet["action"] not in BUY_ACTIONS:
            continue
        signal_date = _date(packet["signal_date"])
        key = (packet["symbol"], signal_date)
        candidate = candidates.get(key)
        if candidate is None:
            raise RuntimeError(f"missing frozen candidate: {key}")
        technical = float(candidate["technical_score"])
        fundamental = (float(packet["formal_score"]) - 0.60 * technical) / 0.40
        values = _features(
            symbol=packet["symbol"], signal_date=signal_date,
            candidate=candidate, actual=actual,
        )
        if values["ma60_slope_5"] is None:
            missing_slope += 1
            values["ma60_slope_5"] = calibration.ma60_slope.median
        scored = shadow_score(
            {
                "technical_score": technical,
                "ma20_ma60_expansion": values["ma20_ma60_expansion"],
                "ma60_slope_5": values["ma60_slope_5"],
            },
            calibration,
        )
        rows.append({
            "packet_id": packet["packet_id"],
            "symbol": packet["symbol"],
            "signal_date": signal_date,
            "technical_score": technical,
            "fundamental_score": fundamental,
            "formal_score": float(packet["formal_score"]),
            "b_score": float(scored["shadow_technical_score"]),
            "total_penalty": float(scored["total_penalty"]),
            **values,
        })
    ranked = assign_ranking_scores(rows)
    print(f"ranking rows={len(ranked)} missing_slope={missing_slope}")
    return ranked


def _position_weights(outcomes: list[dict[str, Any]]) -> dict[str, float]:
    sizer = PositionSizer()
    weights = {}
    for item in outcomes:
        packet = item["packet"]
        if packet["layer"] != "CORE" or packet["action"] not in BUY_ACTIONS:
            continue
        sized = sizer.size(SizerInput(
            formal_score=float(packet["formal_score"]),
            confidence=float(packet["confidence"]),
            data_status=DataStatus.FRESH,
            veto_triggered=bool(packet["veto_triggered"]),
            action=Action(packet["action"]),
        ))
        weights[packet["packet_id"]] = (
            0.0 if sized.recommended_batches <= 0
            else sized.target_position_ratio / sized.recommended_batches
        )
    return weights


def _constraint_results(
    outcomes: list[dict[str, Any]], actual: Any, benchmarks: Any,
    market_dates: list[date], memberships: Any,
) -> tuple[dict[int, list[TimelineTradeResult]], dict[str, dict[str, Any]]]:
    outcome_by_packet = {
        item["packet"]["packet_id"]: item for item in outcomes
        if item["packet"]["layer"] == "CORE"
    }
    symbols = sorted({
        item["packet"]["symbol"] for item in outcomes
        if item["packet"]["layer"] == "CORE"
    })
    bars_by_symbol = {
        symbol: _bars_for_sample(actual, market_dates, symbol)
        for symbol in symbols
    }
    samples = []
    for item in outcomes:
        packet = item["packet"]
        if (
            packet["layer"] != "CORE"
            or packet["action"] not in BUY_ACTIONS
            or not item["entered_preferred_zone"]
            or item["preferred_entry_date"] is None
        ):
            continue
        signal_date = _date(packet["signal_date"])
        industry = _industry_code(memberships, packet["symbol"], signal_date)
        samples.append(TimelineTradeSample(
            packet_id=packet["packet_id"],
            symbol=packet["symbol"],
            signal_date=signal_date,
            entry_date=_date(item["preferred_entry_date"]),
            entry_day=int(item["preferred_entry_day"]),
            market_regime=packet["market_regime"],
            core_structure_at_entry=item["core_structure_at_entry"],
            bars=bars_by_symbol[packet["symbol"]],
            csi300_close_by_date=benchmarks["000300.SH"],
            industry_close_by_date=(
                {} if industry is None else benchmarks.get(industry, {})
            ),
        ))
    print(f"preferred constraint samples={len(samples)}")
    return TimelineConstraintValidationService().evaluate_many(samples), outcome_by_packet


def _tail_sets(
    results: list[TimelineTradeResult], eligible_ids: set[str],
) -> tuple[set[str], set[str]]:
    rows = sorted(
        (
            (item.packet_id, float(item.net_return))
            for item in results
            if item.packet_id in eligible_ids
            and item.executed and item.net_return is not None
        ),
        key=lambda item: item[1],
    )
    if not rows:
        return set(), set()
    count = max(1, ceil(len(rows) * 0.10))
    return (
        {item[0] for item in rows[-count:]},
        {item[0] for item in rows[:count]},
    )


def _scope_metrics(
    selected_ids: set[str], eligible_ids: set[str],
    *, constrained: dict[int, list[TimelineTradeResult]],
    outcome_by_packet: dict[str, dict[str, Any]], weights: dict[str, float],
    actual: Any, market_dates: list[date],
) -> dict[str, Any]:
    output: dict[str, Any] = {
        "selected_signal_count": len(selected_ids),
        "entered_preferred_zone": sum(
            outcome_by_packet[item]["entered_preferred_zone"]
            for item in selected_ids if item in outcome_by_packet
        ),
        "horizons": {},
    }
    excursions = [
        outcome_by_packet[item]
        for item in selected_ids
        if item in outcome_by_packet
        and outcome_by_packet[item]["entered_preferred_zone"]
    ]
    mfe = [
        float(item["maximum_favorable_excursion"])
        for item in excursions if item["maximum_favorable_excursion"] is not None
    ]
    mae = [
        float(item["maximum_adverse_excursion"])
        for item in excursions if item["maximum_adverse_excursion"] is not None
    ]
    output["mfe"] = {
        "mean": None if not mfe else fmean(mfe),
        "median": None if not mfe else median(mfe),
    }
    output["mae"] = {
        "mean": None if not mae else fmean(mae),
        "median": None if not mae else median(mae),
    }
    for horizon in HORIZONS:
        selected_results = [
            item for item in constrained[horizon]
            if item.packet_id in selected_ids
        ]
        summary = summarize_results(selected_results, constrained=True)
        details = [asdict(item) for item in selected_results]
        summary["true_portfolio"] = _curve(
            details, weights, actual, market_dates,
        )
        returns = [
            float(item.net_return) for item in selected_results
            if item.executed and item.net_return is not None
        ]
        summary["return_quality"] = distribution_diagnostics(returns)
        winners, losers = _tail_sets(constrained[horizon], eligible_ids)
        executed_ids = {
            item.packet_id for item in selected_results
            if item.executed and item.net_return is not None
        }
        summary["top_10pct_winner_retention"] = (
            None if not winners else len(executed_ids & winners) / len(winners)
        )
        summary["bottom_10pct_loser_avoidance"] = (
            None if not losers else 1 - len(executed_ids & losers) / len(losers)
        )
        output["horizons"][str(horizon)] = summary
    return output


def _factor_diagnostics(
    rows: list[dict[str, Any]], dates: set[date],
    constrained: dict[int, list[TimelineTradeResult]],
) -> dict[str, Any]:
    selected = [row for row in rows if row["signal_date"] in dates]
    output = {}
    for horizon in HORIZONS:
        returns = {
            item.packet_id: float(item.net_return)
            for item in constrained[horizon]
            if item.executed and item.net_return is not None
        }
        output[str(horizon)] = factor_information_coefficients(
            selected, returns, FACTOR_FIELDS,
        )
    return output


def _fold_report(
    fold: Any, rows: list[dict[str, Any]], constrained: Any,
    outcome_by_packet: Any, weights: Any, actual: Any, market_dates: Any,
) -> dict[str, Any]:
    validation_dates = set(fold.validation_dates)
    eligible_ids = {
        str(row["packet_id"]) for row in rows
        if row["signal_date"] in validation_dates
    }
    groups = {}
    for ranking in RANKING_NAMES:
        groups[ranking] = {}
        for fraction in TOP_FRACTIONS:
            selected = select_daily_top_fraction(
                rows,
                ranking=ranking,
                fraction=fraction,
                allowed_dates=validation_dates,
            )
            groups[ranking][f"top_{round(fraction * 100)}"] = _scope_metrics(
                selected, eligible_ids, constrained=constrained,
                outcome_by_packet=outcome_by_packet, weights=weights,
                actual=actual, market_dates=market_dates,
            )
    return {
        "fold": fold.fold,
        "train": [str(fold.train_dates[0]), str(fold.train_dates[-1]), len(fold.train_dates)],
        "embargo": [str(fold.embargo_dates[0]), str(fold.embargo_dates[-1]), len(fold.embargo_dates)],
        "validation": [str(fold.validation_dates[0]), str(fold.validation_dates[-1]), len(fold.validation_dates)],
        "validation_core_buy_signals": len(eligible_ids),
        "ranking_parameters_frozen_before_validation": {
            "A": "original technical score",
            "B": "fixed historical overheat penalty; expansion speed not penalized",
            "C": "equal-weight cross-sectional percentile of B and PIT fundamental score",
            "optimized_weight_count": 0,
        },
        "training_factor_diagnostics": _factor_diagnostics(
            rows, set(fold.train_dates), constrained,
        ),
        "validation_factor_diagnostics": _factor_diagnostics(
            rows, validation_dates, constrained,
        ),
        "groups": groups,
    }


def _direction_consistency(folds: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for challenger in ("B", "C"):
        output[challenger] = {}
        for scope in ("top_10", "top_20", "top_30"):
            output[challenger][scope] = {}
            for horizon in HORIZONS:
                directions = []
                for fold in folds:
                    baseline = fold["groups"]["A"][scope]["horizons"][str(horizon)]
                    current = fold["groups"][challenger][scope]["horizons"][str(horizon)]
                    comparable = all(
                        value is not None
                        for value in (
                            baseline["average_return"], current["average_return"],
                            baseline["median_return"], current["median_return"],
                            baseline["true_portfolio"]["maximum_drawdown"],
                            current["true_portfolio"]["maximum_drawdown"],
                        )
                    )
                    directions.append(
                        None if not comparable else (
                            current["average_return"] > baseline["average_return"]
                            and current["median_return"] > baseline["median_return"]
                            and current["true_portfolio"]["maximum_drawdown"]
                            > baseline["true_portfolio"]["maximum_drawdown"]
                        )
                    )
                usable = [item for item in directions if item is not None]
                output[challenger][scope][str(horizon)] = {
                    "fold_directions": directions,
                    "improved_fold_count": sum(item is True for item in usable),
                    "comparable_fold_count": len(usable),
                    "all_comparable_folds_improved": bool(usable) and all(usable),
                }
    return output


def main() -> int:
    trade_before = _trade_counts()
    outcomes = json.loads(OUTCOME_PATH.read_text(encoding="utf-8"))
    core_symbols = sorted({
        item["packet"]["symbol"] for item in outcomes
        if item["packet"]["layer"] == "CORE"
    })
    actual, _, benchmarks, market_dates, memberships, _ = _load_market_data(
        core_symbols, START, END,
    )
    rows = _ranking_rows(outcomes, actual)
    constrained, outcome_by_packet = _constraint_results(
        outcomes, actual, benchmarks, market_dates, memberships,
    )
    weights = _position_weights(outcomes)
    folds = build_walk_forward_folds(row["signal_date"] for row in rows)
    fold_reports = []
    for fold in folds:
        print(
            f"fold={fold.fold} train={len(fold.train_dates)} "
            f"validation={len(fold.validation_dates)}"
        )
        fold_reports.append(_fold_report(
            fold, rows, constrained, outcome_by_packet, weights,
            actual, market_dates,
        ))
    validation_dates = set().union(
        *(set(fold.validation_dates) for fold in folds)
    )
    aggregate_fold = type("AggregateFold", (), {
        "fold": 0,
        "train_dates": tuple(sorted(set().union(
            *(set(fold.train_dates) for fold in folds)
        ))),
        "embargo_dates": tuple(sorted(set().union(
            *(set(fold.embargo_dates) for fold in folds)
        ))),
        "validation_dates": tuple(sorted(validation_dates)),
    })()
    aggregate = _fold_report(
        aggregate_fold, rows, constrained, outcome_by_packet, weights,
        actual, market_dates,
    )
    trade_after = _trade_counts()
    if trade_before != trade_after:
        raise RuntimeError("trade state changed during ranking research")
    report = {
        "report_name": "AIQUANT-LITE core ranking A/B/C walk-forward",
        "generated_at": datetime.now(TZ).isoformat(),
        "research_only": True,
        "coverage": {
            "start": str(min(row["signal_date"] for row in rows)),
            "end": str(max(row["signal_date"] for row in rows)),
            "core_frozen_packets": sum(
                item["packet"]["layer"] == "CORE" for item in outcomes
            ),
            "core_buy_signals": len(rows),
            "symbols": len({row["symbol"] for row in rows}),
            "walk_forward_folds": len(folds),
            "validation_dates": len(validation_dates),
        },
        "method": {
            "pool_unchanged": True,
            "formal_decisions_reused": True,
            "pit_fundamental_recovered_from_frozen_60_40": True,
            "fundamental_formula": "(frozen_formal_score - 0.60 * technical_score) / 0.40",
            "future_leakage": False,
            "a_share_constraints": {
                "t_plus_one": True,
                "commission": True,
                "stamp_duty": True,
                "slippage": True,
                "limit_up_down": True,
                "suspension": True,
                "one_price_board": True,
            },
            "true_portfolio_curve": {
                "position_sizer_weights": True,
                "overlapping_signals": True,
                "cash_cap": 1.0,
                "actual_exit_dates": True,
            },
            "expansion_speed_penalized": False,
            "auxiliary_variables_used_in_final_ranking": False,
            "complex_models_used": False,
            "optimized_weights": False,
        },
        "folds": fold_reports,
        "aggregate_validation": aggregate,
        "direction_consistency": _direction_consistency(fold_reports),
        "production_parameters_modified": False,
        "orders_created": 0,
        "positions_changed": 0,
        "trade_state_before": trade_before,
        "trade_state_after": trade_after,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"report={REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
