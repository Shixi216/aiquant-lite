"""Independent validation of a research-only technical overheat penalty."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from scripts.run_formal_timeline_replay import _trade_counts
from scripts.run_timeline_quality_validation import _load_market_data
from trading.experiments.overheat_penalty_shadow import (
    calibrate_penalty, shadow_score, top_fraction,
)
from trading.experiments.technical_overheat_shadow import (
    chronological_segments, group_metrics,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "reports" / "formal_history_validation"
SOURCE_PATH = REPORT_DIR / "core-candidate-timing-diagnostics.json"
REPORT_PATH = REPORT_DIR / "overheat-penalty-shadow-validation.json"
START = date(2024, 8, 1)
END = date(2026, 8, 6)


def _moving_averages(actual, symbol: str, day: date) -> tuple[float, float] | None:
    dates = sorted(value for value in actual[symbol] if value <= day)
    if len(dates) < 60:
        return None
    closes = [float(actual[symbol][value].close) for value in dates[-60:]]
    return sum(closes[-20:]) / 20, sum(closes) / 60


def _selection_metrics(
    selected: list[dict[str, Any]],
    *,
    winners: set[str], losers: set[str],
    overheated_errors: set[str],
) -> dict[str, Any]:
    selected_ids = {row["packet_id"] for row in selected}
    result = group_metrics(selected)
    result.update({
        "top_10pct_winner_retention": len(selected_ids & winners) / len(winners),
        "bottom_10pct_loser_exclusion": 1 - len(selected_ids & losers) / len(losers),
        "bottom_10pct_losers_selected": len(selected_ids & losers),
        "bottom_10pct_loser_share": len(selected_ids & losers) / len(selected),
        "high_score_overheated_errors_selected": len(selected_ids & overheated_errors),
        "high_score_overheated_error_share": len(selected_ids & overheated_errors) / len(selected),
    })
    return result


def main() -> int:
    trade_before = _trade_counts()
    source = json.loads(SOURCE_PATH.read_text(encoding="utf-8"))
    rows = source.get("sample_rows", [])
    symbols = sorted({row["symbol"] for row in rows})
    actual, _, _, market_dates, _, _ = _load_market_data(symbols, START, END)
    market_index = {day: index for index, day in enumerate(market_dates)}
    enriched = []
    for raw in rows:
        row = dict(raw)
        signal_date = date.fromisoformat(row["signal_date"])
        index = market_index[signal_date]
        if index < 5:
            continue
        previous_date = market_dates[index - 5]
        current = _moving_averages(actual, row["symbol"], signal_date)
        previous = _moving_averages(actual, row["symbol"], previous_date)
        if current is None or previous is None:
            continue
        ma20, ma60 = current
        old_ma20, old_ma60 = previous
        row.update({
            "ma60_slope_5": ma60 / old_ma60 - 1,
            "ma20_ma60_expansion": ma20 / ma60 - 1,
        })
        enriched.append(row)

    segments = chronological_segments(enriched, research_fraction=.60, embargo_dates=20)
    research = segments["research"]
    validation = segments["validation"]
    calibration = calibrate_penalty(research)
    scored = []
    for value in validation:
        row = dict(value)
        row.update(shadow_score(row, calibration))
        scored.append(row)

    ordered_returns = sorted(scored, key=lambda row: float(row["return_20"]))
    tail_count = max(1, round(len(scored) * .10))
    winners = {row["packet_id"] for row in ordered_returns[-tail_count:]}
    losers = {row["packet_id"] for row in ordered_returns[:tail_count]}
    overheated_errors = {
        row["packet_id"] for row in scored
        if (
            row["ma20_ma60_expansion"] >= calibration.expansion.q75
            or row["ma60_slope_5"] >= calibration.ma60_slope.q75
        ) and float(row["return_20"]) <= 0
    }

    comparisons = {}
    for fraction in (.10, .20, .30):
        label = f"top_{round(fraction * 100)}pct"
        original = top_fraction(scored, "original_technical_score", fraction)
        shadow = top_fraction(scored, "shadow_technical_score", fraction)
        original_ids = {row["packet_id"] for row in original}
        shadow_ids = {row["packet_id"] for row in shadow}
        comparisons[label] = {
            "original_technical_score": _selection_metrics(
                original, winners=winners, losers=losers,
                overheated_errors=overheated_errors,
            ),
            "shadow_technical_score": _selection_metrics(
                shadow, winners=winners, losers=losers,
                overheated_errors=overheated_errors,
            ),
            "selection_overlap": len(original_ids & shadow_ids) / len(original_ids),
        }

    trade_after = _trade_counts()
    if trade_after != trade_before:
        raise RuntimeError("overheat shadow validation changed trade state")
    report = {
        "report_name": "AIQUANT-LITE overheat penalty shadow validation",
        "generated_at": datetime.now().astimezone().isoformat(),
        "research_only": True,
        "formula": (
            "shadow_technical_score = original_technical_score "
            "- accumulated_expansion_penalty - ma60_five_day_slope_penalty"
        ),
        "expansion_speed_penalized": False,
        "parameter_method": {
            "threshold": "research-only Q75",
            "weight": "absolute research Q4-vs-Q1 20d degradation",
            "shape": "starts at one weight at Q75; linear to two weights; capped",
            "grid_search": False,
            "validation_feedback_used": False,
        },
        "calibration": calibration.as_dict(),
        "coverage": {
            "total_usable_samples": len(enriched),
            "research_samples": len(research),
            "embargo_samples": len(segments["embargo"]),
            "independent_validation_samples": len(scored),
            "global_validation_top_winners": len(winners),
            "global_validation_bottom_losers": len(losers),
        },
        "validation_comparison": comparisons,
        "validation_overheated_losing_candidates": len(overheated_errors),
        "production_parameters_modified": False,
        "shadow_score_connected_to_production": False,
        "orders_created": 0,
        "positions_changed": 0,
        "trade_state_before": trade_before,
        "trade_state_after": trade_after,
    }
    REPORT_PATH.write_text(json.dumps(
        report, ensure_ascii=False, indent=2, default=str,
    ), encoding="utf-8")
    print(json.dumps({
        "calibration": report["calibration"],
        "coverage": report["coverage"],
        "validation_comparison": comparisons,
        "trade_state_unchanged": True,
    }, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
